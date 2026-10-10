# OpenFOAM Foundation 13 and original native audit

Urban Air Studio uses OpenFOAM Foundation 13, pinned to official source commit
18870c24d21c6b982e2cdec27b2f59738cca5f90. Upstream is maintained by the
OpenFOAM Foundation and CFD Direct: https://github.com/OpenFOAM/OpenFOAM-13.
The locally installed official Ubuntu package is 20260624, SHA256
ed083f8b5ad7b8e8e5f46a01bd71ba31857c7d9572d0875496e41a7683241b4e.

The original `science/phase_b/native/uaMeshAudit` utility links the upstream
GPL libraries and is licensed GPL-3.0-or-later. The complete upstream GPLv3
text is retained in `GPL-3.0.txt`. The Python executor, generated case inputs
and imported measurement records do not copy upstream solver source.

No upstream solver binary, complete source tree or container is redistributed
by the current source checkpoint. Local runtime source and licence files remain
available in the approved Ubuntu installation. Any future binary distribution
must retain the appropriate notices and corresponding-source obligations.

The official package identity and original checker are installation evidence;
they do not establish independent physical validation or scientific approval.
