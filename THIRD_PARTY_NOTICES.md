# 第三方组件与来源说明

本项目自己的代码和文档按根目录 [LICENSE](LICENSE) 中的 MIT License 发布。该许可不替代下列组件的许可证，也不授予学校名称、标识、网站内容、通知正文或附件的权利。

## 运行时与 Windows 发布包

源码运行只使用 Python 标准库，不要求安装第三方 Python 包。Windows 免安装包由 PyInstaller 制作，包含 Python 运行环境、所需标准库及相关二进制组件。

| 组件 | 本版本构建信息 | 用途与许可说明 |
| --- | --- | --- |
| CPython / Python 标准库 | 3.12.10，Windows x64 | 运行解释器、本机 HTTP 服务、网页解析、网络请求与缓存；保留 [Python-LICENSE.txt](packaging/licenses/Python-LICENSE.txt) 中的 Python 许可历史及所列组件声明，包括 bzip2、libffi 等。 |
| OpenSSL | 3.0.16，11 Feb 2025 | HTTPS 请求所需的 `libcrypto-3.dll`、`libssl-3.dll`；Apache-2.0，保留 [OpenSSL-LICENSE.txt](packaging/licenses/OpenSSL-LICENSE.txt)，原文来自 [OpenSSL 对应版本](https://github.com/openssl/openssl/blob/openssl-3.0.16/LICENSE.txt)。 |
| PyInstaller 引导器和运行支持文件 | 6.22.0 | 将 Python 程序打包为 Windows 可执行目录；保留 [PyInstaller-COPYING.txt](packaging/licenses/PyInstaller-COPYING.txt)，包含 GPL 条款、Bootloader Exception、运行时钩子的 Apache 2.0 条款等。 |
| Microsoft Windows / Visual C++ 运行库 | 实际收集的 `VCRUNTIME140.dll`、`ucrtbase.dll` 及 `api-ms-win-*.dll` | 由 Microsoft 提供，另受适用的 Microsoft 许可条款约束；本项目 MIT 许可和 Python 许可不替代这些条款。CPython 的 Windows 构建与 C 运行库依赖见 [官方说明](https://github.com/python/cpython/blob/v3.12.10/PCbuild/readme.txt)。 |

上表版本按本版本构建解释器及实际 `dist/HDUNotice/_internal/` 文件核对；其中 OpenSSL 版本由该解释器的 `ssl.OPENSSL_VERSION` 确认。Python 许可文件中的组件声明仅适用于其中列明的内容，不代表所有随包文件都按 Python 许可发布。

请在转发免安装包时保留完整 `licenses/` 目录；不要只复制 EXE 或用本项目的 MIT 许可覆盖第三方许可。本文件中的本地许可链接采用源码仓库路径；免安装包中对应原文位于程序同级的 `licenses/` 目录。PyInstaller 的许可与例外以随附原文为准，也可查阅其 [官方许可页面](https://pyinstaller.org/en/stable/license.html)。

## 构建工具

[packaging/build-requirements.txt](packaging/build-requirements.txt) 固定了以下构建环境依赖。版本与许可名称已经按本版本构建环境中实际安装的 `*.dist-info/METADATA` 和随附许可文件核对。

| 构建工具 | 版本 | 随发行包声明的许可 |
| --- | --- | --- |
| PyInstaller | 6.22.0 | GPL-2.0-or-later，附 Bootloader Exception；运行时钩子按 Apache-2.0 等相应条款，见 [许可原文](packaging/licenses/PyInstaller-COPYING.txt) |
| pyinstaller-hooks-contrib | 2026.8 | 普通 hooks 为 GPL-2.0-or-later；运行时 hooks 为 Apache-2.0，见 [许可原文](packaging/licenses/pyinstaller-hooks-contrib-LICENSE.txt) |
| altgraph | 0.17.5 | MIT，见 [许可原文](packaging/licenses/altgraph-LICENSE.txt) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause，保留 [Apache 2.0 原文](packaging/licenses/packaging-LICENSE.APACHE.txt) 与 [BSD 原文](packaging/licenses/packaging-LICENSE.BSD.txt) |
| pefile | 2024.8.26 | MIT，见 [许可原文](packaging/licenses/pefile-LICENSE.txt) |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause，见 [许可原文](packaging/licenses/pywin32-ctypes-LICENSE.txt) |
| setuptools | 84.0.0 | MIT，见 [许可原文](packaging/licenses/setuptools-LICENSE.txt) |

这些工具用于制作发布包，不是源码用户需要安装的运行依赖。表中列出构建工具不表示每个工具都被复制进最终可执行程序。

各构建工具仍遵循其自身发行包携带的许可。重建发布包时，应根据实际安装版本的元数据和许可证文件重新核对清单；若加入新的第三方运行组件，应同步保留其完整版权与许可文本，并更新本说明。

## 上游贡献与通知内容

早期版本中包含朋友的贡献，已获得用于本项目开源的授权。当前公开文档不推定未确认的署名；已有第三方代码的原版权与许可声明应继续保留。

程序读取 `sources.py` 配置的公开通知列表，只整理来源、标题、发布日期和原文链接。通知正文和附件由对应官方站点提供，部分原文可能位于微信公众号等外部平台。相关内容及标识归各自权利人所有，本项目的开源许可不改变其权利状态。

本项目非杭州电子科技大学及各来源单位的官方产品，不代表学校或相关网站运营方。

## Windows 安装程序

Setup.exe 由 [NSIS](https://nsis.sourceforge.io/) 制作；安装脚本位于 `packaging/installer.nsi`，构建工具 `makensis` 与 Python 运行依赖分离。本次使用已安装的 Unicode NSIS（编译器报告 `v27-Jul-2026.cvs`），安装引导器和相关组件适用 [NSIS 官方许可](https://nsis.sourceforge.io/License)，不由本项目 MIT 许可替代。
