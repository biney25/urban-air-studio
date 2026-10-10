// Original Urban Air Studio independent support audit, GPL-3.0-or-later.
// Reads an existing native case and writes only the operator-selected new report.
#include "argList.H"
#include "Time.H"
#include "timeSelector.H"
#include "fvMesh.H"
#include "volFields.H"
#include "IOdictionary.H"
#include "meshSearch.H"
#include "interpolationCellPoint.H"
#include <fstream>
#include <iomanip>
#include <cmath>
using namespace Foam;

int main(int argc,char* argv[])
{
    argList::noParallel();
    timeSelector::addOptions();
    argList::addOption("report","absolute-path","New independent report outside the original case");
    #include "setRootCase.H"
    #include "createTime.H"
    const instantList times=timeSelector::select0(runTime,args);
    if (times.size()!=1 || !args.optionFound("report")) return 2;
    runTime.setTime(times[0],0);
    #include "createMesh.H"
    const fileName report(args.option("report"));
    if (report.empty() || report[0]!='/' || std::ifstream(report.c_str()).good()) return 3;
    IOdictionary request(IOobject("uaMeshAuditDict",runTime.system(),mesh,IOobject::MUST_READ,IOobject::NO_WRITE));
    const vectorField points(request.lookup("receptorPoints"));
    if (points.empty() || points.size()>1000) return 4;
    volScalarField C(IOobject("C",runTime.name(),mesh,IOobject::MUST_READ,IOobject::NO_WRITE),mesh);
    volVectorField U(IOobject("U",runTime.name(),mesh,IOobject::MUST_READ,IOobject::NO_WRITE),mesh);
    volScalarField k(IOobject("k",runTime.name(),mesh,IOobject::MUST_READ,IOobject::NO_WRITE),mesh);
    volScalarField epsilon(IOobject("epsilon",runTime.name(),mesh,IOobject::MUST_READ,IOobject::NO_WRITE),mesh);
    volScalarField nut(IOobject("nut",runTime.name(),mesh,IOobject::MUST_READ,IOobject::NO_WRITE),mesh);
    if (C.dimensions()!=dimensionSet(1,-3,0,0,0,0,0) || U.dimensions()!=dimensionSet(0,1,-1,0,0,0,0)
        || k.dimensions()!=dimensionSet(0,2,-2,0,0,0,0) || epsilon.dimensions()!=dimensionSet(0,2,-3,0,0,0,0)
        || nut.dimensions()!=dimensionSet(0,2,-1,0,0,0,0)) return 5;
    const interpolationCellPoint<scalar> Cpoint(C),kpoint(k),epoint(epsilon),npoint(nut);
    const interpolationCellPoint<vector> Upoint(U);
    std::ofstream out(report.c_str());
    if (!out.good()) return 6;
    out << std::setprecision(17) << "{\"schema_version\":\"phase-b-native-support-audit-v1\",\"iteration\":" << runTime.value()
        << ",\"cells\":" << mesh.nCells() << ",\"interpolation\":\"Foundation13 interpolationCellPoint\",\"receptors\":[";
    forAll(points,i)
    {
        const vector& p=points[i];
        const label cell=meshSearch::New(mesh).findCell(p);
        if (cell<0 || cell>=mesh.nCells()) return 7;
        const vector centre(mesh.C()[cell]);
        const scalar interpolatedC=Cpoint.interpolate(p,cell);
        const vector interpolatedU=Upoint.interpolate(p,cell);
        const scalar interpolatedK=kpoint.interpolate(p,cell),interpolatedE=epoint.interpolate(p,cell),interpolatedN=npoint.interpolate(p,cell);
        if (!std::isfinite(interpolatedC) || !std::isfinite(interpolatedK) || !std::isfinite(interpolatedE) || !std::isfinite(interpolatedN)
            || !std::isfinite(interpolatedU.x()) || !std::isfinite(interpolatedU.y()) || !std::isfinite(interpolatedU.z())) return 8;
        if (i) out << ',';
        out << "{\"index\":" << i << ",\"cell\":" << cell << ",\"requested_m\":[" << p.x() << ',' << p.y() << ',' << p.z()
            << "],\"cell_centre_m\":[" << centre.x() << ',' << centre.y() << ',' << centre.z()
            << "],\"cell_volume_m3\":" << mesh.V()[cell] << ",\"cell_C_kg_m3\":" << C[cell]
            << ",\"cell_U_m_s\":[" << U[cell].x() << ',' << U[cell].y() << ',' << U[cell].z()
            << "],\"point_C_kg_m3\":" << interpolatedC << ",\"point_U_m_s\":[" << interpolatedU.x() << ',' << interpolatedU.y() << ',' << interpolatedU.z()
            << "],\"cell_k_m2_s2\":" << k[cell] << ",\"point_k_m2_s2\":" << interpolatedK
            << ",\"cell_epsilon_m2_s3\":" << epsilon[cell] << ",\"point_epsilon_m2_s3\":" << interpolatedE
            << ",\"cell_nut_m2_s\":" << nut[cell] << ",\"point_nut_m2_s\":" << interpolatedN << '}';
    }
    out << "]}" << std::endl;
    return out.good()?0:9;
}
