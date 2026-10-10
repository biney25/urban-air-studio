// Urban Air Studio original manufactured scalar diagnostic, GPL-3.0-or-later.
// Separate mathematical verification utility, not the production transport job.
#include "argList.H"
#include "Time.H"
#include "fvMesh.H"
#include "volFields.H"
#include "surfaceFields.H"
#include "IOdictionary.H"
#include "fvmDiv.H"
#include "fvmLaplacian.H"
#include "fvcFlux.H"
#include "fvcSnGrad.H"
#include <fstream>
#include <iomanip>
#include <cmath>

using namespace Foam;

int main(int argc, char *argv[])
{
    argList::noParallel();
    #include "setRootCase.H"
    #include "createTime.H"
    #include "createMesh.H"
    IOdictionary request
    (
        IOobject("uaScalarManufacturedDict",runTime.system(),mesh,IOobject::MUST_READ,IOobject::NO_WRITE)
    );
    const word mode(request.lookup("mode"));
    const label axialCells=readLabel(request.lookup("expectedAxialCells"));
    const label transverseCells=readLabel(request.lookup("expectedTransverseCells"));
    const label expectedCells=axialCells*transverseCells*transverseCells;
    const scalar length=readScalar(request.lookup("length"));
    const scalar width=readScalar(request.lookup("transverseWidth"));
    const scalar speed=readScalar(request.lookup("speed"));
    const scalar molecular=readScalar(request.lookup("molecularDiffusivity"));
    const scalar eddy=readScalar(request.lookup("prescribedNut"));
    const scalar schmidt=readScalar(request.lookup("turbulentSchmidt"));
    const scalar background=readScalar(request.lookup("background"));
    const scalar amplitude=readScalar(request.lookup("amplitude"));
    if ((mode!="sinusoid" && mode!="linear") || axialCells<16 || axialCells>256 || transverseCells!=4
        || mesh.nCells()!=expectedCells || length!=1 || width!=1.0/64 || !std::isfinite(speed)
        || speed<0 || speed>1 || !std::isfinite(molecular) || molecular<0
        || !std::isfinite(eddy) || eddy<0 || !std::isfinite(schmidt) || schmidt<=0
        || !std::isfinite(background) || !std::isfinite(amplitude)
        || amplitude<=0 || background<=amplitude) return 2;
    const scalar diffusivity=molecular+eddy/schmidt;
    if (diffusivity<=0 || diffusivity>.1) return 3;
    volScalarField C
    (
        IOobject("C",runTime.name(),mesh,IOobject::MUST_READ,IOobject::AUTO_WRITE),mesh
    );
    volVectorField U
    (
        IOobject("U",runTime.name(),mesh,IOobject::MUST_READ,IOobject::NO_WRITE),mesh
    );
    if (C.dimensions()!=dimensionSet(1,-3,0,0,0,0,0)
        || U.dimensions()!=dimensionSet(0,1,-1,0,0,0,0)) return 4;
    const surfaceScalarField phi
    (
        IOobject("phi",runTime.name(),mesh,IOobject::NO_READ,IOobject::NO_WRITE),fvc::flux(U)
    );
    volScalarField forcing
    (
        IOobject("uaManufacturedSource",runTime.name(),mesh,IOobject::NO_READ,IOobject::NO_WRITE),
        mesh,dimensionedScalar("S",dimensionSet(1,-3,-1,0,0,0,0),0)
    );
    scalarField exact(expectedCells,0);
    const scalar wave=2*std::acos(-1.0)/length;
    const scalar dx=length/axialCells;
    forAll(C,cell)
    {
        const vector centre(mesh.C()[cell]);
        const scalar volume=mesh.V()[cell];
        const scalar wantedVolume=dx*width*width/(transverseCells*transverseCells);
        if (!std::isfinite(volume) || std::abs(volume-wantedVolume)>wantedVolume*1e-10
            || centre.y()<=0 || centre.y()>=width || centre.z()<=0 || centre.z()>=width
            || centre.x()<0 || centre.x()>length || mag(U[cell]-vector(speed,0,0))>1e-12) return 5;
        const scalar left=centre.x()-dx/2,right=centre.x()+dx/2;
        if (mode=="sinusoid")
        {
            exact[cell]=background+amplitude*(std::cos(wave*left)-std::cos(wave*right))/(wave*dx);
            forcing[cell]=speed*amplitude*(std::sin(wave*right)-std::sin(wave*left))/dx
                +diffusivity*amplitude*wave*(std::cos(wave*left)-std::cos(wave*right))/dx;
        }
        else
        {
            exact[cell]=background+amplitude*centre.x()/length;
            forcing[cell]=speed*amplitude/length;
        }
    }
    const dimensionedScalar D("D",dimensionSet(0,2,-1,0,0,0,0),diffusivity);
    fvScalarMatrix equation(fvm::div(phi,C)-fvm::laplacian(D,C)==forcing);
    const auto performance=equation.solve();
    C.correctBoundaryConditions();
    C.write();
    const surfaceScalarField gradient(fvc::snGrad(C));
    const surfaceScalarField advectiveFlux(fvc::flux(phi,C,"div(phi,C)"));
    scalar errorSquared=0,volumeSum=0,source=0,sourceGross=0,outward=0,outwardGross=0;
    scalar minimum=GREAT,maximum=-GREAT;
    forAll(C,cell)
    {
        if (!std::isfinite(C[cell])) return 6;
        const scalar volume=mesh.V()[cell],error=C[cell]-exact[cell];
        errorSquared+=volume*error*error;
        volumeSum+=volume;
        source+=volume*forcing[cell];
        sourceGross+=volume*std::abs(forcing[cell]);
        minimum=std::min(minimum,C[cell]);maximum=std::max(maximum,C[cell]);
    }
    std::ofstream out((runTime.path()/"uaScalarManufactured.json").c_str());
    if (!out.good()) return 7;
    out << std::setprecision(17)
        << "{\"schema_version\":\"phase-b-manufactured-native-v1\",\"mode\":\"" << mode.c_str()
        << "\",\"cells\":" << mesh.nCells() << ",\"axial_cells\":" << axialCells
        << ",\"transverse_cells\":" << transverseCells << ",\"dx_m\":" << dx
        << ",\"transverse_width_m\":" << width << ",\"cross_section_m2\":" << width*width
        << ",\"diffusivity_m2_s\":" << diffusivity << ",\"speed_m_s\":" << speed
        << ",\"C_dimensions\":[1,-3,0,0,0,0,0],\"source_dimensions\":[1,-3,-1,0,0,0,0],"
           "\"normalized_volume_L2_error\":" << std::sqrt(errorSquared/volumeSum)/amplitude
        << ",\"minimum_C_kg_m3\":" << minimum << ",\"maximum_C_kg_m3\":" << maximum
        << ",\"linear_initial_residual\":" << performance.initialResidual()
        << ",\"linear_final_residual\":" << performance.finalResidual()
        << ",\"linear_iterations\":" << performance.nIterations()
        << ",\"integrated_source_kg_s\":" << source
        << ",\"gross_source_kg_s\":" << sourceGross << ",\"patch_fluxes\":[";
    forAll(mesh.boundary(),patchi)
    {
        const fvPatch& patch=mesh.boundary()[patchi];
        scalar advective=0,diffusive=0;
        forAll(patch,face)
        {
            advective+=advectiveFlux.boundaryField()[patchi][face];
            diffusive-=diffusivity*gradient.boundaryField()[patchi][face]*patch.magSf()[face];
        }
        outward+=advective+diffusive;
        outwardGross+=std::abs(advective)+std::abs(diffusive);
        if (patchi) out << ',';
        out << "{\"name\":\"" << patch.name().c_str() << "\",\"advective_outward_kg_s\":" << advective
            << ",\"diffusive_outward_kg_s\":" << diffusive << '}';
    }
    out << "],\"net_outward_kg_s\":" << outward
        << ",\"gross_boundary_kg_s\":" << outwardGross
        << ",\"scalar_cell_values_kg_m3\":[";
    forAll(C,cell) { if (cell) out << ',';out << C[cell]; }
    out << "],\"exact_cell_averages_kg_m3\":[";
    forAll(exact,cell) { if (cell) out << ',';out << exact[cell]; }
    out << "],\"source_cell_averages_kg_m3_s\":[";
    forAll(forcing,cell) { if (cell) out << ',';out << forcing[cell]; }
    out << "],\"cell_centres_x_m\":[";
    forAll(C,cell) { if (cell) out << ',';out << mesh.C()[cell].x(); }
    out << "]}" << std::endl;
    return out.good()?0:8;
}
