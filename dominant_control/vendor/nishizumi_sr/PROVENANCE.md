# Original Nishizumi SR sources

These files are kept unmodified, exactly like the ones in
`vendor/nishizumi_tools`. Every adaptation lives in `dominant_control/tools/sr`
(the engine, wired to the shared telemetry) and in
`dominant_control/tools/original_overlays/sr_worker.py` (the overlay window).

Origin: `nishizumi-maho/Nishizumi-SR`, commit
`1dc514e49662be4c4a8d37c438d665415bc0c6bd` ("Merge overlay drag/close fix,
integration guide and CI", 2026-08-10).

| Arquivo | SHA-256 |
| --- | --- |
| `app_paths.py` | `ba4c575221b03043746eb629e8a851af66c07f6a6c635727b0327c0043cc6ba2` |
| `iracing/__init__.py` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `iracing/sdk.py` | `16949baab6b2948bb2410fe283ca5b5f4af2ee788b69fec222a5e6faaf88b836` |
| `iracing/session.py` | `81f19a4941aefa0a777d1563f1c6f6e13ae5eba21c6fe613d10e02958bb27139` |
| `iracing/telemetry.py` | `02f74732c335292372eb4005c0028e29d56d99f59a1ce8c6e7c62a9fbdbb7998` |
| `overlay/__init__.py` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `overlay/native.py` | `9e1033c199d66650292260ba87a0cdc5fd68a8c22c4915f7ea81ee498138baec` |
| `overlay/styles.py` | `117ad5ee1294550db0d8637fc69ffe112dafee6c293a3fbecac5748816b85ed5` |
| `overlay/widgets.py` | `625c672ca810f30c35ec18476085f44a0eb98413148edcb727beb75f2f2ba88e` |
| `overlay/window.py` | `598f45bbba0061cb9c571b27bb1f77ed9291c05184ec12e015f83c44c3bc9a80` |
| `sr/__init__.py` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `sr/calculator.py` | `22e0990e0770c332351e5c6db79ac1392513dff6af0c732f43f983f2782bc909` |
| `sr/calibration.py` | `dd50c1ed6545c9d86164b5ab940c535cf744268610867f4e61788077f7827717` |
| `sr/history.py` | `25b4ab64cf56b3fd49dd0d90af16b00639fda96fdaf83867636f4ab548ad03a3` |
| `sr/license_map.py` | `4fc1b330688f7d8b17a4e8ef3590605bff39ecc7f9da53575451a32b0ce7e7dc` |
| `sr/model.py` | `d895ad58cc46c1a381e00af3aaf8c089e32dc0fcc6ef4b13a71eb7dd654ebefd` |
| `tracks/__init__.py` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `tracks/database.py` | `9a64068549f694b06581e7acc30a1cf107fc6318e85e86127622fdae9e97d1c4` |
| `tracks/official_corners.json` | `13273bf3b061856baf7b725e87f707499bd09f7c9446da553d408c22ef57e497` |

## What was not copied, and why

`main.py`, `config/settings.py`, `sr/license_targets.py`, `tools/` and `tests/`
from the original project were left out: they are the standalone application
(its own telemetry loop, its own `settings.json`, its own tray icon and global
hotkeys) or command-line utilities. Inside this application that role belongs
to `dominant_control/tools/sr/engine.py`, which runs on the shared
`TelemetryHub` — one telemetry connection for the whole application, as with
every other tool.

## Why this folder is not a Python package

There is no `__init__.py` here on purpose. The Nishizumi SR modules import each
other by the names they have in that project (`from sr.model import ...`,
`from overlay.styles import ...`, `from app_paths import user_file`), and
keeping the files intact means those names have to resolve.
`dominant_control/tools/sr/vendored.py` is what makes them resolve: it appends
this folder to the end of `sys.path` and redirects the writable folder before
any of those imports run.

## When updating

Replace only the original files, update the hashes above and run
`tests\run_tests.py`.
