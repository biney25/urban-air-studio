"""Original controlled dictionaries for the audited Foundation 13 source.

No OpenCFD options, external includes, shell scripts or coded objects are emitted.
"""
import math

KAPPA, CMU, C1, C2 = 0.4, 0.09, 1.44, 1.92
SIGMA_EPS = KAPPA*KAPPA/((C2-C1)*math.sqrt(CMU))


def fmt(value):
    return format(float(value), ".16g")


def vector(values):
    return "(" + " ".join(fmt(v) for v in values) + ")"


def header(name, cls="dictionary"):
    return f"// Urban Air Studio; OpenFOAM Foundation 13; preparation only\nFoamFile\n{{\n    format ascii;\n    class {cls};\n    object {name};\n}}\n\n"


def abl_profile(config, z):
    ustar = KAPPA*config.reference_speed_m_s/math.log((config.reference_height_m+config.roughness_length_m)/config.roughness_length_m)
    return {"friction_velocity_m_s": ustar,
            "speed_m_s": ustar/KAPPA*math.log((z+config.roughness_length_m)/config.roughness_length_m),
            "k_m2_s2": ustar*ustar/math.sqrt(CMU),
            "epsilon_m2_s3": ustar**3/(KAPPA*(z+config.roughness_length_m))}


def abl_entries(config):
    return f"""flowDir (1 0 0);
        zDir (0 0 1);
        Uref {fmt(config.reference_speed_m_s)};
        Zref {fmt(config.reference_height_m)};
        z0 uniform {fmt(config.roughness_length_m)};
        zGround uniform 0;
        kappa {fmt(KAPPA)};
        Cmu {fmt(CMU)};"""


QUALITY = """maxNonOrtho 65;
maxBoundarySkewness 20;
maxInternalSkewness 4;
maxConcave 80;
minVol 1e-15;
minTetQuality 1e-15;
minTwist 0.02;
minDeterminant 0.001;
minFaceWeight 0.05;
minVolRatio 0.01;
nSmoothScale 4;
errorReduction 0.75;
"""


def obj_surface(mesh):
    lines = ["# Original closed local-metre building extrusion; Foundation 13 triSurface input"]
    lines += ["v " + " ".join(fmt(v) for v in p) for p in mesh["vertices"]]
    lines += ["f " + " ".join(str(i+1) for i in face) for face in mesh["faces"]]
    return "\n".join(lines)+"\n"


def mesh_files(solids, sources, domain, config):
    low, high, counts = domain["min_m"], domain["max_m"], domain["base_grid_counts"]
    a, b, c = low
    d, e, f = high
    verts = [[a,b,c],[d,b,c],[d,e,c],[a,e,c],[a,b,f],[d,b,f],[d,e,f],[a,e,f]]
    block = header("blockMeshDict") + "convertToMeters 1;\nvertices\n(\n" + "\n".join("    "+vector(p) for p in verts)
    block += "\n);\nblocks\n(\n    hex (0 1 2 3 4 5 6 7) ("+" ".join(str(n) for n in counts)+") simpleGrading (1 1 1)\n);\nedges ();\nboundary\n(\n"
    for name, typ, face in [("inlet","patch","0 4 7 3"),("outlet","patch","1 2 6 5"),
                            ("ground","wall","0 3 2 1"),("top","patch","4 5 6 7"),
                            ("side_left","symmetryPlane","0 1 5 4"),("side_right","symmetryPlane","3 7 6 2")]:
        block += f"    {name} {{ type {typ}; faces (({face})); }}\n"
    block += ");\nmergePatchPairs ();\n"
    geometries, refinements, regions = [], [], []
    files = {"system/blockMeshDict": block, "system/meshQualityDict": header("meshQualityDict")+QUALITY}
    for solid in solids:
        name = solid["patch"]
        files[f"constant/geometry/{name}.obj"] = obj_surface(solid["mesh"])
        geometries.append(f'    {name} {{ type triSurface; file "{name}.obj"; }}')
        refinements.append(f"        {name} {{ level ({config.surface_refinement_level} {config.surface_refinement_level}); patchInfo {{ type wall; }} }}")
    for source in sources:
        name, support = source["zone"], source["support_box_solver_m"]
        geometries.append(f"    {name}_box {{ type box; min {vector(support['min'])}; max {vector(support['max'])}; }}")
        regions.append(f"        {name}_box {{ mode inside; level {config.surface_refinement_level}; }}")
    snappy = header("snappyHexMeshDict")+f"""castellatedMesh true;
snap true;
addLayers false;
geometry
{{
{chr(10).join(geometries)}
}}
castellatedMeshControls
{{
    maxLocalCells {config.max_cells};
    maxGlobalCells {config.max_cells};
    minRefinementCells 0;
    maxLoadUnbalance 0.1;
    nCellsBetweenLevels 3;
    resolveFeatureAngle 30;
    allowFreeStandingZoneFaces false;
    extendedRefinementSpan true;
    features ();
    refinementSurfaces
    {{
{chr(10).join(refinements)}
    }}
    refinementRegions
    {{
{chr(10).join(regions)}
    }}
    insidePoint {vector(domain['snappy_inside_point_m'])};
}}
snapControls
{{
    nSmoothPatch 3;
    tolerance 2;
    nSolveIter 100;
    nRelaxIter 5;
    nFeatureSnapIter 10;
    explicitFeatureSnap false;
    multiRegionFeatureSnap false;
    implicitFeatureSnap true;
}}
addLayersControls
{{
    layers {{}}
    relativeSizes true;
    expansionRatio 1.2;
    finalLayerThickness 0.5;
    minThickness 0.001;
    featureAngle 100;
    slipFeatureAngle 30;
    nLayerIter 50;
    nRelaxedIter 20;
    nRelaxIter 5;
    nGrow 0;
    nSmoothSurfaceNormals 1;
    nSmoothNormals 3;
    nSmoothThickness 10;
    maxFaceThicknessRatio 0.5;
    maxThicknessToMedialRatio 0.6;
    minMedialAxisAngle 90;
    nMedialAxisIter 10;
    nBufferCellsNoExtrude 0;
    additionalReporting false;
}}
meshQualityControls
{{
{QUALITY}
}}
writeFlags ();
debug 0;
mergeTolerance 1e-6;
"""
    # Modern Foundation 13 uses type box/level and insidePoint. These are not
    # the OpenCFD searchableBox/levels/locationInMesh dictionaries.
    files["system/snappyHexMeshDict"] = snappy
    actions = []
    for source in sources:
        name, support = source["zone"], source["support_box_solver_m"]
        actions.extend([f"""    {{
        name {name}_cells;
        type cellSet;
        action new;
        source boxToCell;
        sourceInfo {{ box {vector(support['min'])} {vector(support['max'])}; }}
    }}""", f"""    {{
        name {name};
        type cellZoneSet;
        action new;
        source setToCellZone;
        sourceInfo {{ set {name}_cells; }}
    }}"""])
    files["system/topoSetDict"] = header("topoSetDict")+"actions\n(\n"+"\n".join(actions)+"\n);\n"
    return files


def field(name, dimensions, initial, inlet, outlet, ground, buildings, top, solids):
    cls = "volVectorField" if name == "U" else "volScalarField"
    text = header(name, cls)+f"dimensions {dimensions};\ninternalField uniform {initial};\nboundaryField\n{{\n"
    patches = [("inlet",inlet),("outlet",outlet),("ground",ground),("top",top),
               ("side_left","type symmetryPlane;"),("side_right","type symmetryPlane;")]
    patches += [(s["patch"],buildings) for s in solids]
    for patch, entries in patches:
        text += f"    {patch}\n    {{\n        {entries}\n    }}\n"
    return text+"}\n"


def fields(solids, domain, config):
    ref, top = abl_profile(config, config.reference_height_m), abl_profile(config, domain["max_m"][2])
    init_u, top_u = vector([config.reference_speed_m_s,0,0]), vector([top["speed_m_s"],0,0])
    inlet_abl = abl_entries(config)
    k, eps, top_eps = fmt(ref["k_m2_s2"]), fmt(ref["epsilon_m2_s3"]), fmt(top["epsilon_m2_s3"])
    background = fmt(config.background_ug_m3*1e-9)
    data = {}
    data["0/U"] = field("U","[0 1 -1 0 0 0 0]",init_u,
        f"type atmBoundaryLayerInletVelocity;\n        {inlet_abl}\n        value uniform {init_u};",
        "type pressureInletOutletVelocity; value uniform (0 0 0);", "type noSlip;", "type noSlip;",
        f"type fixedValue; value uniform {top_u};", solids)
    data["0/p"] = field("p","[0 2 -2 0 0 0 0]","0","type zeroGradient;",
        "type fixedValue; value uniform 0;","type zeroGradient;","type zeroGradient;","type zeroGradient;",solids)
    data["0/k"] = field("k","[0 2 -2 0 0 0 0]",k,
        f"type atmBoundaryLayerInletK;\n        {inlet_abl}\n        value uniform {k};",
        f"type inletOutlet; inletValue uniform {k}; value uniform {k};",
        f"type kqRWallFunction; value uniform {k};",f"type kqRWallFunction; value uniform {k};",
        f"type fixedValue; value uniform {k};",solids)
    # Foundation epsilonWallFunction reads kappa/Cmu from the matching nut wall.
    wall_eps = f"type epsilonWallFunction; value uniform {eps};"
    data["0/epsilon"] = field("epsilon","[0 2 -3 0 0 0 0]",eps,
        f"type atmBoundaryLayerInletEpsilon;\n        {inlet_abl}\n        value uniform {eps};",
        f"type inletOutlet; inletValue uniform {eps}; value uniform {eps};",wall_eps,wall_eps,
        f"type fixedValue; value uniform {top_eps};",solids)
    rough = f"type nutkAtmRoughWallFunction; z0 uniform {fmt(config.roughness_length_m)}; kappa {fmt(KAPPA)}; Cmu {fmt(CMU)}; value uniform 0;"
    smooth = f"type nutkWallFunction; kappa {fmt(KAPPA)}; Cmu {fmt(CMU)}; value uniform 0;"
    data["0/nut"] = field("nut","[0 2 -1 0 0 0 0]","0","type calculated; value uniform 0;",
        "type calculated; value uniform 0;",rough,smooth,"type calculated; value uniform 0;",solids)
    data["0/C"] = field("C","[1 -3 0 0 0 0 0]",background,
        f"type fixedValue; value uniform {background};",
        f"type inletOutlet; inletValue uniform {background}; value uniform {background};",
        "type zeroGradient;","type zeroGradient;","type zeroGradient;",solids)
    data["system/setAtmBoundaryLayerDict"] = header("setAtmBoundaryLayerDict")+abl_entries(config)+"\n"
    return data


def flow_scalar_files(sources, receptors, config):
    files = {}
    files["constant/physicalProperties"] = header("physicalProperties")+f"viscosityModel constant;\nnu [0 2 -1 0 0 0 0] {fmt(config.kinematic_viscosity_m2_s)};\n"
    files["constant/momentumTransport"] = header("momentumTransport")+f"""simulationType RAS;
RAS
{{
    model kEpsilon;
    turbulence on;
    printCoeffs on;
    viscosityModel Newtonian;
    kEpsilonCoeffs
    {{
        Cmu {fmt(CMU)};
        C1 {fmt(C1)};
        C2 {fmt(C2)};
        sigmak 1;
        sigmaEps {fmt(SIGMA_EPS)};
    }}
}}
"""
    models = []
    for source in sources:
        models.append(f"""{source['zone']}_emission
{{
    type semiImplicitSource;
    cellZone {source['zone']};
    volumeMode absolute;
    sources
    {{
        C {{ explicit {fmt(source['rate_kg_s'])}; implicit 0; }}
    }}
}}""")
    files["constant/fvModels"] = header("fvModels")+"\n".join(models)+"\n"
    files["constant/fvConstraints"] = header("fvConstraints")
    files["system/fvSchemes"] = header("fvSchemes")+"""ddtSchemes { default steadyState; }
gradSchemes { default Gauss linear; }
divSchemes
{
    default none;
    div(phi,U) bounded Gauss upwind;
    div(phi,k) bounded Gauss upwind;
    div(phi,epsilon) bounded Gauss upwind;
    div(phi,C) Gauss upwind;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
wallDist { method meshWave; }
"""
    # C uses conservative upwind rather than adding an artificial continuity
    # correction. Positivity, conservation and diffusion error need actual runs.
    files["system/fvSolution"] = header("fvSolution")+f"""solvers
{{
    p {{ solver GAMG; tolerance {fmt(config.residual_tolerance)}; relTol 0.1; smoother GaussSeidel; }}
    pcorr {{ solver GAMG; tolerance {fmt(config.residual_tolerance)}; relTol 0; smoother GaussSeidel; }}
    "(U|k|epsilon)" {{ solver smoothSolver; smoother symGaussSeidel; tolerance {fmt(config.residual_tolerance)}; relTol 0.1; }}
    C {{ solver smoothSolver; smoother symGaussSeidel; tolerance {fmt(config.scalar_residual_tolerance)}; relTol 0; }}
}}
SIMPLE {{ nNonOrthogonalCorrectors 1; consistent yes; }}
relaxationFactors
{{
    fields {{ p 0.3; }}
    equations {{ U 0.7; k 0.7; epsilon 0.7; C 0.7; }}
}}
"""
    probes = "\n".join("            "+vector(r["position_solver_m"]) for r in receptors)
    interval = max(1, config.flow_iterations//10)
    files["system/controlDict"] = header("controlDict")+f"""application foamRun;
solver incompressibleFluid;
libs ("libatmosphericModels.so");
startFrom startTime;
startTime 0;
stopAt endTime;
endTime {config.flow_iterations};
deltaT 1;
writeControl timeStep;
writeInterval {interval};
purgeWrite 2;
writeFormat ascii;
writePrecision 12;
writeCompression off;
timeFormat general;
timePrecision 12;
runTimeModifiable false;
cacheTemporaryObjects (kEpsilon:G);
functions
{{
    transportC
    {{
        type scalarTransport;
        libs ("libsolverFunctionObjects.so");
        field C;
        schemesField C;
        phi phi;
        diffusivity viscosity;
        alphal {fmt(config.molecular_diffusivity_m2_s/config.kinematic_viscosity_m2_s)};
        alphat {fmt(1/config.turbulent_schmidt_number)};
        nCorrectors 1;
        executeControl timeStep;
        executeInterval 1;
        writeControl timeStep;
        writeInterval {interval};
    }}
    receptors
    {{
        type probes;
        libs ("libsampling.so");
        fields (U C);
        interpolationScheme cell;
        fixedLocations true;
        probeLocations
        (
{probes}
        );
        writeControl timeStep;
        writeInterval 1;
    }}
}}
"""
    return files
