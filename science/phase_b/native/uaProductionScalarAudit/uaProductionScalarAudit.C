// Urban Air Studio original read-only production scalar audit, GPL-3.0-or-later.
// This utility does NOT solve or reproduce scalarTransport's equation.
// foamRun/libsolverFunctionObjects performs the actual production-path solve.
#include "argList.H"
#include "Time.H"
#include "timeSelector.H"
#include "fvMesh.H"
#include "volFields.H"
#include "surfaceFields.H"
#include "viscosityModel.H"
#include "incompressibleMomentumTransportModel.H"
#include "fvModels.H"
#include "fvMatrices.H"
#include "fvcFlux.H"
#include "fvcSnGrad.H"
#include "surfaceInterpolate.H"
#include <fstream>
#include <iomanip>
#include <cmath>

using namespace Foam;

static void values(std::ofstream& out, const scalarField& field)
{
    out << '[';
    forAll(field, i)
    {
        if (!std::isfinite(field[i])) FatalErrorInFunction << "Nonfinite native field" << exit(FatalError);
        if (i) out << ',';
        out << field[i];
    }
    out << ']';
}

int main(int argc, char *argv[])
{
    argList::noParallel();
    timeSelector::addOptions();
    #include "setRootCase.H"
    #include "createTime.H"
    const instantList selected(timeSelector::select0(runTime, args));
    if (selected.size()!=2 || selected.first().value()<=0) return 2;
    runTime.setTime(selected.first(),0);
    #include "createMesh.H"
    const dictionary& functions=runTime.controlDict().subDict("functions");
    const dictionary& transport=functions.subDict("transportC");
    const word diffusivity(transport.lookup("diffusivity"));
    if (word(transport.lookup("type"))!="scalarTransport"
        || word(transport.lookup("field"))!="C"
        || (diffusivity!="viscosity" && diffusivity!="constant")) return 3;
    const scalar alphal=diffusivity=="viscosity"?readScalar(transport.lookup("alphal")):0;
    const scalar alphat=diffusivity=="viscosity"?readScalar(transport.lookup("alphat")):0;
    const scalar constantD=diffusivity=="constant"?readScalar(transport.lookup("D")):0;
    std::ofstream out((runTime.path()/"uaProductionScalarAudit.json").c_str(),std::ios::out|std::ios::trunc);
    if (!out.good()) return 4;
    out << std::setprecision(17)
        << "{\"schema_version\":\"phase-b-production-scalar-audit-v1\",\"solver\":\"foamRun incompressibleFluid\","
           "\"scalar_solver\":\"scalarTransport function object\",\"auditor_solves_equation\":false,"
           "\"cells\":" << mesh.nCells() << ",\"diffusivity_branch\":\"" << diffusivity.c_str()
        << "\",\"volumes_m3\":";
    values(out,mesh.V());
    out << ",\"centres_m\":[";
    forAll(mesh.C(),i)
    {
        if (i) out << ',';
        const vector p(mesh.C()[i]);
        out << '[' << p.x() << ',' << p.y() << ',' << p.z() << ']';
    }
    out << "],\"zones\":[";
    forAll(mesh.cellZones(),zonei)
    {
        if (zonei) out << ',';
        const cellZone& zone=mesh.cellZones()[zonei];
        scalar volume=0;forAll(zone,i) volume+=mesh.V()[zone[i]];
        out << "{\"name\":\"" << zone.name().c_str() << "\",\"cells\":" << zone.size()
            << ",\"volume_m3\":" << volume << '}';
    }
    out << "],\"snapshots\":[";
    forAll(selected,timei)
    {
        if (timei) out << ',';
        runTime.setTime(selected[timei],timei);
        volScalarField C(IOobject("C",runTime.name(),mesh,IOobject::MUST_READ,IOobject::NO_WRITE),mesh);
        volVectorField U(IOobject("U",runTime.name(),mesh,IOobject::MUST_READ,IOobject::NO_WRITE),mesh);
        surfaceScalarField phi(IOobject("phi",runTime.name(),mesh,IOobject::MUST_READ,IOobject::NO_WRITE),mesh);
        autoPtr<viscosityModel> viscosity(viscosityModel::New(mesh));
        autoPtr<incompressible::momentumTransportModel> momentum
        (
            incompressible::momentumTransportModel::New(U,phi,viscosity)
        );
        momentum->validate();
        const volScalarField nu(momentum->nu());
        const volScalarField nut(momentum->nut());
        volScalarField D(IOobject("uaAuditD",runTime.name(),mesh,IOobject::NO_READ,IOobject::NO_WRITE),
                         mesh,dimensionedScalar(dimKinematicViscosity,constantD));
        if (diffusivity=="viscosity") D=alphal*nu+alphat*nut;
        const tmp<fvScalarMatrix> sourceMatrix=fvModels::New(mesh).source(C);
        // fvMatrix += Su stores -V*Su; RHS scalarTransport uses the matrix with
        // opposite sign. Read the actual native source instead of inferring it.
        scalarField sourceRates(-sourceMatrix().source());
        const surfaceScalarField advective(fvc::flux(phi,C,"div(phi,C)"));
        const surfaceScalarField diffusive(-fvc::interpolate(D)*fvc::snGrad(C)*mesh.magSf());
        if (C.dimensions()!=dimensionSet(1,-3,0,0,0,0,0)
            || sourceMatrix().dimensions()!=dimensionSet(1,0,-1,0,0,0,0)
            || phi.dimensions()!=dimVolume/dimTime) return 5;
        out << "{\"iteration\":" << runTime.value() << ",\"scalar_dimensions\":[1,-3,0,0,0,0,0],"
               "\"source_dimensions\":[1,0,-1,0,0,0,0],\"scalar_kg_m3\":";
        values(out,C.primitiveField());
        out << ",\"native_source_rates_kg_s\":";values(out,sourceRates);
        out << ",\"native_source_diagonal\":";values(out,sourceMatrix().diag());
        out << ",\"nu_m2_s\":";values(out,nu.primitiveField());
        out << ",\"nut_m2_s\":";values(out,nut.primitiveField());
        out << ",\"D_m2_s\":";values(out,D.primitiveField());
        out << ",\"U_m_s\":[";
        forAll(U,i)
        {
            if (i) out << ',';
            const vector u(U[i]);out << '[' << u.x() << ',' << u.y() << ',' << u.z() << ']';
        }
        out << "],\"patch_fluxes\":[";
        forAll(mesh.boundary(),patchi)
        {
            if (patchi) out << ',';
            scalar a=0,d=0,v=0;
            forAll(mesh.boundary()[patchi],i)
            {
                a+=advective.boundaryField()[patchi][i];
                d+=diffusive.boundaryField()[patchi][i];
                v+=phi.boundaryField()[patchi][i];
            }
            out << "{\"name\":\"" << mesh.boundary()[patchi].name().c_str()
                << "\",\"advective_outward_kg_s\":" << a << ",\"diffusive_outward_kg_s\":" << d
                << ",\"volume_outward_m3_s\":" << v << '}';
        }
        out << "]}";
    }
    out << "]}" << std::endl;
    return out.good()?0:6;
}
