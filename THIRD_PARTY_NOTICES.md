# Third-Party Notices

The packaged Roster Tool application (macOS `Roster Tool.app`, Windows
`Roster Tool.exe` folder) bundles the components below. This file is
included in the packaged app. Where a component ships its own license
texts, they are included in the package inside that component's metadata
folder (`*.dist-info/`), or under `licenses/` for the Python runtime.

This is an engineering inventory to support IT and compliance review. It is
not a legal opinion.

| Component | Version | License | Where the license text is in the package |
|---|---|---|---|
| Python runtime | 3.13 (macOS build), 3.12 (Windows build) | Python Software Foundation License | `licenses/Python/LICENSE.txt` |
| pypdfium2 | 5.15.2 | Apache-2.0 or BSD-3-Clause | `pypdfium2-5.15.2.dist-info/licenses/LICENSES/` |
| PDFium (native PDF engine bundled by pypdfium2) | per pypdfium2 | BSD-3-Clause, plus its own third-party components (see the note below) | `pypdfium2-5.15.2.dist-info/licenses/BUILD_LICENSES/` |
| ReportLab | 5.1.0 | BSD-3-Clause (Copyright (c) 2000-2025, ReportLab Inc.) | `reportlab-5.1.0.dist-info/licenses/LICENSE` |
| Pillow (dependency of ReportLab) | 13.0.0 | MIT-CMU (HPND), with the licenses of its bundled image libraries | `pillow-13.0.0.dist-info/licenses/LICENSE` |
| openpyxl | 3.1.5 | MIT | `openpyxl-3.1.5.dist-info/LICENCE.rst` (also reproduced below) |
| et_xmlfile (dependency of openpyxl) | 2.0.0 | MIT | `et_xmlfile-2.0.0.dist-info/LICENCE.rst` (also reproduced below) |
| charset-normalizer (dependency of ReportLab) | 3.5.2 | MIT | Reproduced below |
| DejaVu Sans Condensed fonts | 2.37 | Bitstream Vera / DejaVu font license (permissive) | `roster_tool/assets/fonts/DejaVu-LICENSE.txt` |
| PyInstaller bootloader | 6.22.3 | GPL-2.0-or-later **with the PyInstaller bootloader exception**, which allows distributing applications built with PyInstaller under any terms | `pyinstaller-6.22.3.dist-info/` |

**PDFium's third-party components** (license texts in `BUILD_LICENSES`):
abseil, agg23, bigint, brotli, dragonbox, fast_float, fdlibm, fp16, FreeType,
HarfBuzz, Highway, ICU, inspector_protocol, Little CMS, libjpeg-turbo, OpenJPEG,
libpng, libtiff, libyuv, llvm-libc, simdutf, strongtalk, V8 and zlib.

**Not included in the package:**
* ReportLab's optional demo fonts (some GPL-licensed), which Roster Tool does
  not use. The build removes them.
* Development tools such as pytest.

## MIT License: openpyxl 3.1.5, et_xmlfile 2.0.0 and charset-normalizer 3.5.2

openpyxl: Copyright (c) 2010 openpyxl
et_xmlfile: Copyright (c) 2010-2015 openpyxl
charset-normalizer: Copyright (c) 2025 TAHRI Ahmed R.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
