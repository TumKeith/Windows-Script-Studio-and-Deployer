# ⚡ WinScript Studio & Fleet Orchestrator

An enterprise-grade Windows script editor, standalone binary compiler, and multi-threaded domain subnet deployment engine. WinScript Studio provides a unified workstation environment to author, sandbox-test, package, and silently orchestrate administrative tasks across Active Directory subnets.

---

## 🌟 Key Capabilities

* **Integrated Monaco Editor:** Full VS Code-style editor supporting syntax highlighting for PowerShell (`.ps1`), Python (`.py`), and Batch (`.bat`).
* **Local Test Execution Sandbox:** Safely execute scripts locally and capture real-time `stdout`, `stderr`, and exit codes before deployment.
* **Automated Binary Compiler:** Package scripts into self-contained, standalone `.exe` binaries using `PyInstaller` (Python) and `PS2EXE` (PowerShell).
* **Smart Device Filtering:** Probes SMB (`445`) and WinRM (`5985`) to ensure only actual Windows PC endpoints are targeted while automatically ignoring IoT appliances, smart TVs, and network printers.
* **Concurrent Subnet Deployment:** Multi-threaded orchestrator pushes binaries to remote administrative shares (`C$\Windows\Temp`), triggers silent non-interactive execution, and cleans up artifacts automatically.
* **Native Desktop GUI:** Runs as a standalone Windows desktop console powered by Microsoft Edge WebView2 (`pywebview`).

---

## 🚀 Getting Started

### 1. Prerequisites

* Windows 10 / 11 / Server
* Python 3.10+
* PowerShell 5.1+ / PowerShell 7+

### 2. Environment Setup

```powershell
# Clone the repository
git clone [https://github.com/YOUR_USERNAME/windows-script-studio-and-deployer.git](https://github.com/YOUR_USERNAME/windows-script-studio-and-deployer.git)
cd windows-script-studio-and-deployer

# Install dependencies
python -m pip install fastapi uvicorn pywebview pyinstaller
(Optional: Install PS2EXE for PowerShell executable compilation)

PowerShell
Install-Module -Name ps2exe -Scope CurrentUser -Force
3. Launching the Studio
PowerShell
python studio.py
4. Compiling into a Standalone Desktop Executable
PowerShell
python -m PyInstaller --onefile --noconsole --name "WinScriptStudio" studio.py
The compiled standalone tool will be generated in the dist\ directory.

🛡️ Execution & Audit Notes
Remote actions execute in Session 0 (non-interactive system context) to ensure end-user workflows are not interrupted.

All remote sessions, authentications, and script block activities remain fully visible in standard Windows Event Logs (Security Event ID 4624/4672 and PowerShell/Operational Event ID 4104).

📄 License
MIT License. See LICENSE for details.


---
