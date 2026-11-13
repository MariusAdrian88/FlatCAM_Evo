# Linux and macOS release packages

`.github/workflows/release-packages.yml` builds these assets with cx_Freeze:

- `FlatCAM_EVO-<tag>-linux-x86_64.AppImage`
- `FlatCAM_EVO-<tag>-linux-x86_64.AppImage.zsync` (for AppImageUpdate; the AppImage embeds
  `gh-releases-zsync|<owner>|<repo>|latest|FlatCAM_EVO-*-linux-x86_64.AppImage.zsync`)
- `FlatCAM_EVO-<tag>-macos-arm64.dmg` (Apple Silicon)
- `FlatCAM_EVO-<tag>-macos-x86_64.dmg` (Intel)

## Publish a release

1. Commit and push the workflow and scripts before tagging the release. The tagged source must contain
   `scripts/freeze_release.py`, `scripts/check_release.py` and `requirements.txt`.
2. Publish a GitHub Release, or publish a prerelease. Saving a draft does not start a build.
3. Open **Actions → Release Linux and macOS packages** to follow the three independent jobs.
4. Each successful job checks frozen startup, saves a workflow artifact and attaches its package to the release.
   Packages appear after the release is published, as the jobs finish.

The workflow uses GitHub's built-in token with `contents: write`. No personal access token or Apple credentials
are needed. GitHub Actions must be enabled and repository/organization policy must permit release uploads.

Upload the Windows installer and Windows updater bundle yourself. The automated jobs replace only an asset with
their own exact filename when rerun; they don't delete other release assets or change release notes.

If publishing through another workflow, note that releases created with its built-in `GITHUB_TOKEN` do not trigger
another release workflow. Use the manual run below, or explicitly dispatch this workflow from that automation.

## Retry or build for an existing release

Rerun a failed job from its Actions run, or select **Run workflow** and enter the existing release tag. Manual
dispatch requires this workflow on the default branch. Select a workflow branch with the desired workflow fixes;
application source is still checked out from `refs/tags/<tag>`, not from that branch or the latest commit.
An unknown release/tag fails rather than creating a new release. Duplicate runs for the same tag are serialized.

## Packaging configuration

`scripts/freeze_release.py` is the tracked Linux/macOS cx_Freeze configuration. The local, git-ignored Windows
`make_freeze.py` is not required. It follows that script's `lib/` resource layout and includes the dynamically
loaded OpenGL and OR-Tools modules and the in-app update-checking package. It doesn't build the Windows updater.

Builds use Python 3.13 and conda-forge for GDAL, Rasterio (1.4.0 or newer), FreeType, Tcl/Tk and NumPy 2. Conda
installs NumPy, so pip doesn't replace the NumPy that the native libraries are built against. The Python requirements
come from `requirements.txt`. OR-Tools is pinned to 9.14.6206 and cx_Freeze to 8.7.3.

Linux builds run on Ubuntu 22.04. This doesn't guarantee compatibility with every Linux distribution: native
library requirements and host graphics drivers still matter. The AppImage tool and runtime are downloaded by
cx_Freeze. The build uses extraction mode so FUSE isn't required on the runner.

macOS builds use macOS 15 runners for each CPU architecture. Apps are ad-hoc signed and signatures verified before
the DMG is assembled. They aren't Developer ID signed or notarized. Users may need to approve the app through
**System Settings → Privacy & Security → Open Anyway** after trying to open it. Copy the app to Applications first.
These packages should not be advertised as supporting macOS versions older than the build and dependency baseline.

The Apple Silicon (`arm64`) package targets M1 and later M-series processors natively. The macOS bundle's name is
**FlatCAM Evo**. Its application version and numeric build identifier come from `App.version` and `App.version_date`
in the tagged `appMain.py`: for example, version `1.0` dated `2026/10/25` becomes application version `1.0.0` and build
`2026.10.25`. Reading these literals doesn't import or start the application during packaging. Asset filenames still
use the full release tag.

## Validation

Before uploading, `scripts/check_release.py` starts the frozen program from a temporary directory with separate
settings and no inherited Python, Qt, Tcl or loader path overrides. It requires the bundled default preprocessor
and compiled translations, and parses every bundled translation catalog to reject corrupt files. Its Tcl command
checks that the running app registered the default preprocessor and can find translations from its application
directory, then requires a success marker and a zero exit status. Linux checks the extracted final AppImage; macOS
checks the signed app used in the DMG. This catches missing startup imports, basic Qt/Tcl failures, missing or corrupt
resources and working-directory assumptions.

The headless check doesn't exercise the VisPy/OpenGL GUI, all CAM operations or Gatekeeper on another machine.
Test the first downloaded packages on clean Linux and macOS machines before treating them as validated releases.
