# Original Nishizumi Tools sources

These files are kept unmodified. Every adaptation lives in
`dominant_control/tools/original_overlays`.

| Arquivo | Origem | SHA-256 |
| --- | --- | --- |
| `Nishizumi_FuelMonitor.py` | arquivo fornecido `Nishizumi_FuelMonitor(1).py` | `eba2aae91236fe2d7b32d4bdb8e31a855033ecee638a37abab17c1aee3e53906` |
| `Nishizumi_CautionOverlay.py` | `nishizumi-maho/Nishizumi-Tools`, commit `b0b9d85a2b0d5e262f576ecc3bb244e4856bd4a0` | `47b93e49c6bc850d0ddff40d5b9a0a524776cb237ac4af62ee62a30b583ac916` |
| `Nishizumi_TireWear.py` | `nishizumi-maho/Nishizumi-Tools`, commit `ffa9d1c273332e419c5c9829f96b6951f0e8b982` | `46be785b60dc86f47c3c100716dd4ffeb1fcc9dacc222ed823890d62a9c51afd` |
| `Nishizumi_Traction.py` | `nishizumi-maho/Nishizumi-Tools`, commit `ffa9d1c273332e419c5c9829f96b6951f0e8b982` | `e44c283127c14ab1881b91a476e3b4d24a808c0265bff1008485a551ae627702` |
| `nishizumi_pitcalibrator.py` | `nishizumi-maho/Nishizumi-Tools`, commit `ffa9d1c273332e419c5c9829f96b6951f0e8b982` | `e126922eac3db4169e831476a133d06519ff4e2140d048c6d1a63f5fc6d819e2` |

When updating a tool, replace only the original file, update its hash and
adjust and test the matching adapter. The integrated engines still own the
state, so a visual change does not affect the other modules.

## Caution Overlay

The Caution Overlay is still a pre-release in Nishizumi Tools (its
`build-caution-overlay.yml` workflow publishes the .exe with `--prerelease`)
and it is the only file in this folder that is not part of that project's
single-executable bundle. It is copied like the others; the equivalent engine,
wired to the shared `TelemetryHub`, is in
`dominant_control/tools/caution/engine.py`, and the window is in
`dominant_control/tools/original_overlays/caution_worker.py`.

## One exception to "unmodified"

The FuelMonitor is the one vendored tool written in Brazilian Portuguese, and
this application is English. Its wording — and only its wording — is translated
when the source is packaged; the hash above is the hash of the original file.
