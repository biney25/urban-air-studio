"""Auditable arithmetic/QC, not a model-validation or regulatory service."""
from collections import Counter, defaultdict
from datetime import timedelta, timezone
import math

from evidence.schemas import Inventory, MeasurementCampaign, ReplayProtocol, ReplayAssessment, timestamp

MOLAR_MASS_G_MOL = {'NO':30.006, 'NO2':46.0055, 'CO':28.010, 'O3':47.9982}
GAS_CONSTANT_J_MOL_K = 8.31446261815324


def inventory_summary(payload):
    model = Inventory.model_validate(payload)
    segments = []
    for segment in model.segments:
        total = lower = upper = 0.0
        for fleet in segment.fleet:
            source = fleet.vehicles_per_hour * segment.length_m/1000 * fleet.factor_g_per_vehicle_km / 3600
            total += source
            lower += source * (1-fleet.count_uncertainty_fraction) * (1-fleet.factor_uncertainty_fraction)
            upper += source * (1+fleet.count_uncertainty_fraction) * (1+fleet.factor_uncertainty_fraction)
        segments.append({'id':segment.id, 'length_m':segment.length_m, 'rate_g_s':total,
                         'line_rate_g_s_m':total/segment.length_m,
                         'range_g_s':[lower,upper]})
    background = model.background
    return {'schema_version':'inventory-summary-v1', 'species':model.species,
            'interval_start_utc':timestamp(model.interval_start).astimezone(timezone.utc).isoformat(),
            'interval_end_utc':timestamp(model.interval_end).astimezone(timezone.utc).isoformat(),
            'segments':segments, 'total_rate_g_s':sum(row['rate_g_s'] for row in segments),
            'total_rate_kg_s':sum(row['rate_g_s'] for row in segments)/1000,
            'total_range_g_s':[sum(row['range_g_s'][i] for row in segments) for i in (0,1)],
            'background_mode':background.mode,
            'background_local_adjustment_ug_m3':background.local_baseline_ug_m3,
            'qualification':'inventory_arithmetic_only',
            'limitations':['Activity and emission factors are supplied inputs, not measured or scientifically approved by upload.',
                           'Bounds are simultaneous parameter ranges, not confidence intervals; no independence or annual weighting is assumed.',
                           'Coarse-total mode requires a compatible paired local increment; never add the full local simulation again.',
                           'Inventory is not automatically assigned to the point-source research worker.']}


def measurement_summary(payload):
    model = MeasurementCampaign.model_validate(payload)
    instruments = {row.id:row for row in model.instruments}
    rows, groups = [], defaultdict(list)
    for observation in model.observations:
        instrument = instruments[observation.instrument_id]
        start = timestamp(observation.interval_start)-timedelta(seconds=instrument.clock_offset_seconds)
        end = timestamp(observation.interval_end)-timedelta(seconds=instrument.clock_offset_seconds)
        flags = []
        value = observation.raw_value
        canonical = None
        quarantine = observation.provider_qa in {'rejected','missing'} or value is None
        if value is None or observation.provider_qa == 'missing':
            flags.append('missing_reading')
        if observation.provider_qa == 'rejected':
            flags.append('provider_rejected')
        if observation.provider_qa == 'flagged':
            flags.append('provider_flagged')
        if value is not None:
            if observation.raw_unit == 'ug/m3':
                canonical = value
            elif observation.raw_unit == 'mg/m3':
                canonical = value*1000
            elif instrument.species not in MOLAR_MASS_G_MOL:
                flags.append('mixing_ratio_species_unsupported')
                quarantine = True
            elif observation.temperature_k is None or observation.pressure_pa is None:
                flags.append('mixing_ratio_requires_temperature_pressure')
                quarantine = True
            else:
                ppb = value*(1000 if observation.raw_unit == 'ppm' else 1)
                canonical = ppb * MOLAR_MASS_G_MOL[instrument.species] * observation.pressure_pa / (GAS_CONSTANT_J_MOL_K * observation.temperature_k)*1e-3
        if canonical is not None and canonical < 0:
            flags.append('negative_observed_noise_retained')
        if not instrument.calibration_version or instrument.calibration_uncertainty_ug_m3 is None:
            flags.append('calibration_unqualified')
        if not instrument.collocation_record:
            flags.append('collocation_missing')
        row = {'id':observation.id, 'instrument_id':instrument.id, 'upstream_id':instrument.upstream_id,
               'location_id':instrument.location_id, 'species':instrument.species,
               'raw_value':value, 'raw_unit':observation.raw_unit, 'value_ug_m3':canonical,
               'interval_start_utc':start.astimezone(timezone.utc).isoformat(),
               'interval_end_utc':end.astimezone(timezone.utc).isoformat(),
               'clock_offset_seconds':instrument.clock_offset_seconds,
               'flags':flags, 'qc_state':'quarantined' if quarantine else ('flagged' if flags else 'accepted')}
        rows.append(row)
        groups[(instrument.upstream_id,instrument.species)].append((start,end,len(rows)-1))
    # All overlapping records from the same physical instrument are quarantined.
    # Never choose a favoured duplicate silently; different averaging supports stay visible.
    for entries in groups.values():
        entries.sort(key=lambda item:item[0])
        active = []
        for start,end,index in entries:
            active = [(a_end,a_index) for a_end,a_index in active if a_end > start]
            for _, other in active:
                for affected in (index,other):
                    row = rows[affected]
                    if 'overlapping_upstream_interval' not in row['flags']:
                        row['flags'].append('overlapping_upstream_interval')
                    row['qc_state'] = 'quarantined'
            active.append((end,index))
    return {'schema_version':'campaign-qc-v1','rows':rows,
            'counts':dict(Counter(row['qc_state'] for row in rows)),
            'row_count':len(rows), 'instrument_count':len(instruments),
            'qualification':'automated_qc_only',
            'limits':['Raw readings and uncertainty/calibration metadata remain unchanged; flags are not scientific sign-off.',
                      'Clock offset means instrument time minus true time; canonical timestamps subtract it.',
                      'Spikes are not automatically deleted. Negative measurement noise is retained without clipping.',
                      'This does not correct sensor drift, certify collocation, measure accuracy or calculate AQI.']}


def validate_protocol_campaign(payload, campaign_payload):
    protocol = ReplayProtocol.model_validate(payload)
    campaign = MeasurementCampaign.model_validate(campaign_payload)
    locations = {instrument.location_id for instrument in campaign.instruments}
    requested = set(protocol.calibration_locations+protocol.evaluation_locations)
    if not requested <= locations:
        raise ValueError('Replay location partitions must resolve to the frozen campaign')
    assigned = {}
    physical_locations = {}
    for instrument in campaign.instruments:
        group = 'calibration' if instrument.location_id in protocol.calibration_locations else 'evaluation' if instrument.location_id in protocol.evaluation_locations else None
        if group is not None:
            prior = assigned.setdefault(instrument.upstream_id,group)
            if prior != group:
                raise ValueError('An upstream instrument cannot leak between calibration and evaluation')
            # Distinct IDs or inlet heights cannot hide the same horizontal site
            # in both partitions. Near-site buffers still need study review.
            position = (instrument.longitude,instrument.latitude)
            physical_group = physical_locations.setdefault(position,group)
            if physical_group != group:
                raise ValueError('The same physical location cannot be relabelled into calibration and evaluation')
    return True


def replay_summary(payload):
    protocol = ReplayProtocol.model_validate(payload)
    return {'schema_version':'replay-protocol-summary-v1',
            'campaign_evidence_id':protocol.campaign_evidence_id,
            'calibration_location_count':len(protocol.calibration_locations),
            'evaluation_location_count':len(protocol.evaluation_locations),
            'calibration_locations':protocol.calibration_locations,
            'evaluation_locations':protocol.evaluation_locations,
            'calibration_blocks':[block.model_dump() for block in protocol.calibration_blocks],
            'evaluation_blocks':[block.model_dump() for block in protocol.evaluation_blocks],
            'thresholds':{'wind_nrmse_limit':protocol.wind_nrmse_limit,
                          'concentration_fac2_min':protocol.concentration_fac2_min,
                          'concentration_floor_ug_m3':protocol.concentration_floor_ug_m3},
            'qualification':'protocol_only; evaluation_not_executed',
            'limits':['Disjoint locations and contiguous time blocks are declared before evaluation; upload alone cannot prove preregistration or blindness.',
                      'Campaign binding and instrument identity are checked; coverage, observed support and model compatibility must be checked before scoring.',
                      'Thresholds are proposed project goals, not regulatory standards or achieved accuracy.']}


def process_evidence(kind, payload):
    return {'inventory':inventory_summary,'measurement_campaign':measurement_summary,'replay_protocol':replay_summary}[kind](payload)


def assess_replay(payload, protocol_payload, campaign_payload):
    """Score imported interval-mean predictions only against frozen held-out support.

    It verifies the submitted arithmetic/support, not the claimed model execution
    or blind preregistration. No error metric promotes a model or a sensor.
    """
    assessment = ReplayAssessment.model_validate(payload)
    protocol = ReplayProtocol.model_validate(protocol_payload)
    validate_protocol_campaign(protocol_payload, campaign_payload)
    qc = measurement_summary(campaign_payload)
    expected = {}
    outside = []
    for row in qc['rows']:
        if row['species'] != assessment.species or row['location_id'] not in protocol.evaluation_locations:
            continue
        start, end = timestamp(row['interval_start_utc']), timestamp(row['interval_end_utc'])
        matched = [index for index,block in enumerate(protocol.evaluation_blocks)
                   if timestamp(block.start) <= start and end <= timestamp(block.end)]
        if len(matched) == 1:
            expected[row['id']] = {**row, 'evaluation_block':matched[0]}
        else:
            outside.append({'observation_id':row['id'],'reason':'not_fully_inside_reserved_time_block'})
    predictions = {row.observation_id:row for row in assessment.predictions}
    if set(predictions) != set(expected):
        missing = sorted(set(expected)-set(predictions))
        extra = sorted(set(predictions)-set(expected))
        raise ValueError(f'Held-out prediction IDs must match exactly; missing={missing[:10]}, unexpected={extra[:10]}')
    rows, exclusions = [], []
    for identifier,row in expected.items():
        prediction = predictions[identifier]
        if (timestamp(prediction.interval_start) != timestamp(row['interval_start_utc']) or
            timestamp(prediction.interval_end) != timestamp(row['interval_end_utc'])):
            raise ValueError('Prediction averaging intervals must match clock-corrected observations exactly; end-state fields cannot become interval means')
        if row['qc_state'] == 'quarantined' or row['value_ug_m3'] is None:
            exclusions.append({'observation_id':identifier,'reason':'qc_quarantined','flags':row['flags']})
            continue
        observed, predicted = row['value_ug_m3'], prediction.value_ug_m3
        # Only the denominator needs a positive floor. A zero prediction against
        # a positive observation is a failure, never an excluded favourable case.
        fac2_eligible = observed > protocol.concentration_floor_ug_m3
        ratio = predicted/observed if fac2_eligible else None
        rows.append({'observation_id':identifier,'location_id':row['location_id'],
                     'evaluation_block':row['evaluation_block'],'observed_ug_m3':observed,'predicted_ug_m3':predicted,
                     'difference_ug_m3':predicted-observed,'qc_flags':row['flags'],
                     'fac2_eligible':fac2_eligible,'prediction_observation_ratio':ratio,
                     'fac2_pass':0.5 <= ratio <= 2 if ratio is not None else None})

    def metrics(group):
        count = len(group)
        eligible = [row for row in group if row['fac2_eligible']]
        return {'count':count,
                'bias_ug_m3':sum(row['difference_ug_m3'] for row in group)/count if count else None,
                'mae_ug_m3':sum(abs(row['difference_ug_m3']) for row in group)/count if count else None,
                'rmse_ug_m3':math.sqrt(sum(row['difference_ug_m3']**2 for row in group)/count) if count else None,
                'fac2':sum(row['fac2_pass'] for row in eligible)/len(eligible) if eligible else None,
                'fac2_eligible_count':len(eligible),'fac2_floor_excluded_count':count-len(eligible)}

    locations = [{'location_id':identifier,**metrics([row for row in rows if row['location_id'] == identifier])}
                 for identifier in protocol.evaluation_locations]
    blocks = [{'evaluation_block':index,**metrics([row for row in rows if row['evaluation_block'] == index])}
              for index in range(len(protocol.evaluation_blocks))]
    aggregate = metrics(rows)
    groups = locations+blocks
    fac2_gate = 'insufficient_evidence' if any(group['fac2'] is None for group in groups) else (
        'passed_arithmetic_target' if all(group['fac2'] >= protocol.concentration_fac2_min for group in groups) else 'failed')
    return {'schema_version':'held-out-replay-assessment-v1','protocol_evidence_id':assessment.protocol_evidence_id,
            'method_id':assessment.method_id,'method_build_hash':assessment.method_build_hash,
            'species':assessment.species,'units':assessment.units,'time_support':assessment.time_support,
            'expected_prediction_count':len(expected),'scored_count':len(rows),
            'aggregate':aggregate,'by_location':locations,'by_time_block':blocks,'rows':rows,
            'qc_exclusions':exclusions,'outside_reserved_support':outside,
            'proposed_fac2_target':protocol.concentration_fac2_min,'fac2_positive_floor_ug_m3':protocol.concentration_floor_ug_m3,
            'fac2_gate':fac2_gate,'wind_gate':'not_evaluated; campaign contains concentration observations only',
            'qualification':'imported_prediction_arithmetic_only','model_qualified':False,
            'limits':['Submitted method identity and interval-mean model values are frozen claims; this service does not attest that their solver ran.',
                      'A passing arithmetic target is not scientific qualification, independent sign-off, decision adequacy or proof of blind preregistration.',
                      'Quarantined readings are listed and retained. Flagged/negative observed noise remains in bias/RMSE with explicit flags.',
                      'FAC2 uses observations above the positive denominator floor; zero/low predictions against eligible observations count as failures. Exclusions are reported, raw readings never clipped.',
                      'Every reserved location/time block is shown; a favourable average cannot conceal an empty or failed group.']}


def paired_decision(pairs, weights, protected_ids, uncertainty_bounds_ug_m3, harm_tolerance_ug_m3=0):
    """Compare compatible nonnegative concentration pairs under declared scenario weights.

    Input uncertainty is a symmetric bound on the *paired difference*, not an
    invented independent baseline/alternative error or confidence interval.
    """
    if not pairs or len(pairs) != len(weights) or any(isinstance(w,bool) or not isinstance(w,(int,float)) or not math.isfinite(w) or w <= 0 for w in weights):
        raise ValueError('One finite positive weight is required per paired case')
    if not math.isclose(sum(weights),1,rel_tol=0,abs_tol=1e-9):
        raise ValueError('Representative weights must sum to one; stress cases require a separate analysis')
    if isinstance(harm_tolerance_ug_m3,bool) or not isinstance(harm_tolerance_ug_m3,(int,float)) or not math.isfinite(harm_tolerance_ug_m3) or harm_tolerance_ug_m3 < 0:
        raise ValueError('Harm tolerance must be finite and nonnegative')
    ids = set(pairs[0]['baseline'])
    if not ids or not set(protected_ids) <= ids or set(uncertainty_bounds_ug_m3) != ids:
        raise ValueError('All receptors and uncertainty bounds must be supplied, including protected neighbours')
    for pair in pairs:
        if set(pair['baseline']) != ids or set(pair['alternative']) != ids or not pair.get('compatibility_verified'):
            raise ValueError('Each pair must have the same receptors and verified method/conditions/support compatibility')
        for value in list(pair['baseline'].values())+list(pair['alternative'].values()):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value < 0:
                raise ValueError('Paired concentration values must be finite and nonnegative')
    rows = []
    for identifier in sorted(ids):
        bound = uncertainty_bounds_ug_m3[identifier]
        if isinstance(bound,bool) or not isinstance(bound,(int,float)) or not math.isfinite(bound) or bound < 0:
            raise ValueError('Paired uncertainty bounds must be finite and nonnegative')
        deltas = [pair['alternative'][identifier]-pair['baseline'][identifier] for pair in pairs]
        delta = sum(weight*change for weight,change in zip(weights,deltas))
        lower,upper = delta-bound,delta+bound
        verdict = 'benefit' if upper < 0 else 'harm' if lower > 0 else 'inconclusive'
        if bound == 0 and delta == 0:
            verdict = 'no_change'
        protected = identifier in protected_ids
        robust_no_harm = max(deltas)+bound <= harm_tolerance_ug_m3 if protected else None
        rows.append({'receptor_id':identifier,'weighted_delta_ug_m3':delta,
                     'paired_range_ug_m3':[lower,upper],'per_case_delta_ug_m3':deltas,
                     'verdict':verdict,'protected':protected,'no_harm_in_all_cases':robust_no_harm})
    return {'rows':rows,'protected_no_harm':all(row['no_harm_in_all_cases'] for row in rows if row['protected']) if protected_ids else None,
            'qualification':'conditional_paired_arithmetic_only',
            'limits':['Weights are supplied scenario frequencies, not verified annual probabilities.',
                      'Uncertainty ranges are supplied symmetric paired bounds, not confidence intervals.',
                      'No physical validation, causal claim, occupancy-weighted dose or professional recommendation follows from this arithmetic.']}
