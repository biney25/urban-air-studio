// Urban Air Studio original native mesh audit, GPL-3.0-or-later.
// Uses OpenFOAM Foundation 13 public mesh APIs; no uploaded code is compiled.
#include "argList.H"
#include "Time.H"
#include "timeSelector.H"
#include "fvMesh.H"
#include "volFields.H"
#include "surfaceFields.H"
#include "IOdictionary.H"
#include "fvcSnGrad.H"
#include "surfaceInterpolate.H"
#include "meshSearch.H"
#include <fstream>
#include <iomanip>
#include <cmath>

using namespace Foam;

int main(int argc, char *argv[])
{
    argList::noParallel();
    timeSelector::addOptions();
    argList::addBoolOption("fields", "audit scalar/velocity and outward boundary transport");
    #include "setRootCase.H"
    #include "createTime.H"
    const instantList selected(timeSelector::select0(runTime, args));
    if (selected.empty()) return 5;
    runTime.setTime(selected.last(), selected.size()-1);
    #include "createMesh.H"

    IOdictionary request
    (
        IOobject
        (
            "uaMeshAuditDict", runTime.system(), mesh,
            IOobject::MUST_READ, IOobject::NO_WRITE
        )
    );
    const pointField points(request.lookup("receptorPoints"));
    const meshSearch& search = meshSearch::New(mesh);
    const bool fields = args.optionFound("fields");
    const fileName destination(runTime.path()/(fields ? "uaFieldAudit.json" : "uaMeshAudit.json"));
    std::ofstream out(destination.c_str(), std::ios::out | std::ios::trunc);
    if (!out.good()) return 2;
    out << std::setprecision(17);
    out << "{\"schema_version\":\"phase-b-native-mesh-audit-v1\","
           "\"cell_count\":" << mesh.nCells()
        << ",\"cell_volumes_m3\":[";
    forAll(mesh.V(), celli)
    {
        const scalar volume = mesh.V()[celli];
        if (!std::isfinite(volume) || volume <= 0) return 3;
        if (celli) out << ',';
        out << volume;
    }
    out << "],\"zones\":[";
    forAll(mesh.cellZones(), zonei)
    {
        const cellZone& zone = mesh.cellZones()[zonei];
        scalar volume = 0;
        forAll(zone, i) volume += mesh.V()[zone[i]];
        if (zonei) out << ',';
        out << "{\"name\":\"" << zone.name().c_str()
            << "\",\"cell_count\":" << zone.size()
            << ",\"volume_m3\":" << volume << '}';
    }
    out << "],\"receptors\":[";
    forAll(points, i)
    {
        if (i) out << ',';
        const label cell = search.findCell(points[i]);
        out << "{\"index\":" << i << ",\"cell\":" << cell;
        if (cell >= 0)
        {
            const vector centre(mesh.C()[cell]);
            out << ",\"cell_centre_m\":[" << centre.x() << ','
                << centre.y() << ',' << centre.z() << ']'
                << ",\"distance_to_cell_centre_m\":"
                << mag(centre-points[i]);
        }
        out << '}';
    }
    out << ']';
    if (fields)
    {
        volScalarField C
        (
            IOobject("C", runTime.name(), mesh, IOobject::MUST_READ, IOobject::NO_WRITE), mesh
        );
        volVectorField U
        (
            IOobject("U", runTime.name(), mesh, IOobject::MUST_READ, IOobject::NO_WRITE), mesh
        );
        volScalarField nut
        (
            IOobject("nut", runTime.name(), mesh, IOobject::MUST_READ, IOobject::NO_WRITE), mesh
        );
        surfaceScalarField phi
        (
            IOobject("phi", runTime.name(), mesh, IOobject::MUST_READ, IOobject::NO_WRITE), mesh
        );
        const scalar molecular = readScalar(request.lookup("molecularDiffusivity"));
        const scalar schmidt = readScalar(request.lookup("turbulentSchmidt"));
        if (!std::isfinite(molecular) || molecular < 0 || !std::isfinite(schmidt) || schmidt <= 0) return 6;
        const volScalarField D
        (
            IOobject("uaD", runTime.name(), mesh, IOobject::NO_READ, IOobject::NO_WRITE),
            dimensionedScalar("Dm", dimensionSet(0,2,-1,0,0,0,0), molecular) + nut/schmidt
        );
        const surfaceScalarField faceD(fvc::interpolate(D));
        const surfaceScalarField gradient(fvc::snGrad(C));
        out << ",\"field_audit\":{\"iteration\":" << runTime.value()
            << ",\"scalar_dimensions\":[";
        for (direction i=0; i<7; ++i)
        {
            if (i) out << ',';
            out << C.dimensions()[i];
        }
        out << "],\"cell_concentrations_kg_m3\":[";
        forAll(C, i)
        {
            if (!std::isfinite(C[i])) return 7;
            if (i) out << ',';
            out << C[i];
        }
        out << "],\"receptor_fields\":[";
        forAll(points, i)
        {
            if (i) out << ',';
            const label cell = search.findCell(points[i]);
            out << "{\"index\":" << i << ",\"cell\":" << cell;
            if (cell >= 0)
            {
                const vector velocity(U[cell]);
                out << ",\"C_kg_m3\":" << C[cell]
                    << ",\"U_m_s\":[" << velocity.x() << ',' << velocity.y() << ',' << velocity.z() << ']';
            }
            out << '}';
        }
        out << "],\"boundary_fluxes\":[";
        forAll(mesh.boundary(), patchi)
        {
            scalar advective = 0, diffusive = 0, volume = 0;
            const fvPatch& patch = mesh.boundary()[patchi];
            forAll(patch, facei)
            {
                const scalar flow = phi.boundaryField()[patchi][facei];
                volume += flow;
                advective += flow*C.boundaryField()[patchi][facei];
                diffusive -= faceD.boundaryField()[patchi][facei]
                    *gradient.boundaryField()[patchi][facei]*patch.magSf()[facei];
            }
            if (!std::isfinite(advective) || !std::isfinite(diffusive) || !std::isfinite(volume)) return 8;
            if (patchi) out << ',';
            out << "{\"patch\":\"" << patch.name().c_str()
                << "\",\"advective_outward_kg_s\":" << advective
                << ",\"diffusive_outward_kg_s\":" << diffusive
                << ",\"volume_outward_m3_s\":" << volume << '}';
        }
        out << "]}";
    }
    out << "}" << std::endl;
    out.close();
    if (!out.good()) return 4;
    Info << "Urban Air Studio native audit written: " << mesh.nCells()
         << " actual fluid cells" << endl;
    return 0;
}
