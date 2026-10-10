# Cedar integration notices

Urban Air Studio uses the AWS-origin open-source Cedar policy engine through the community-maintained `cedarpy` Python binding. This is local library execution, not Amazon Verified Permissions or another hosted AWS service.

- Binding: `cedarpy` 4.12.0, maintained by k9securityio. [Source](https://github.com/k9securityio/cedar-py), [exact package release](https://pypi.org/project/cedarpy/4.12.0/). The binding explicitly states it is not officially supported by AWS or the Cedar Policy team.
- Engine: `cedar-policy`, `cedar-policy-core` and `cedar-policy-formatter` 4.12.0. [Official source](https://github.com/cedar-policy/cedar), [AWS introduction](https://aws.amazon.com/tw/about-aws/whats-new/2023/05/cedar-open-source-language-access-control/).
- The binding's upstream Apache License 2.0 text is preserved in `LICENSE`. The wheel's native SBOM also identifies the three Cedar engine components as Apache-2.0. This notice does not alter those upstream terms or the application's own licensing.
- `cedarpy-4.12.0-windows.cyclonedx.json` and `cedarpy-4.12.0-linux.cyclonedx.json` are the unmodified upstream SBOMs from the two hash-verified CPython 3.13 x64 wheels. They list the bundled native components, package checksums and declared licences. Each installed wheel also retains its upstream licence/SBOM under its package metadata directory.

The Windows native library was executed in the local project environment. The Linux wheel was downloaded and inspected for its checksum, native-library identity and SBOM; no Linux image execution is claimed. The dependency lock supports these two targets explicitly. Other platforms/releases require separate verified identities and tests.

Additional installed libraries retain their own licences and notices. This folder records the direct Cedar integration and upstream native SBOMs, not a blanket licensing certification of the entire application or every future distribution.
