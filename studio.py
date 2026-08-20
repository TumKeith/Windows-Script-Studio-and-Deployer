import os
import sys
import uuid
import socket
import ipaddress
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import webview

app = FastAPI(title="Windows Script & Orchestration Studio")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUILD_DIR = os.path.join(BASE_DIR, "builds")
os.makedirs(BUILD_DIR, exist_ok=True)

# ---------------------------------------------------------
# Helper Functions: Network Validation & Push
# ---------------------------------------------------------
def is_windows_host(ip: str) -> bool:
    """Filters out non-PC appliances by probing SMB (445) and WinRM (5985)."""
    for port in [445, 5985]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex((ip, port)) == 0:
                    return True
        except Exception:
            pass
    return False

def deploy_to_single_pc(ip: str, payload_path: str, user: str, password: str):
    if not is_windows_host(ip):
        return {"ip": ip, "status": "SKIPPED", "detail": "Non-PC device or offline"}

    filename = os.path.basename(payload_path)
    ps_command = f"""
    $secPass = ConvertTo-SecureString '{password}' -AsPlainText -Force
    $cred = New-Object System.Management.Automation.PSCredential('{user}', $secPass)
    
    # Mount Admin Share and copy binary
    New-PSDrive -Name "RemoteTarget" -PSProvider FileSystem -Root "\\\\{ip}\\C$\\Windows\\Temp" -Credential $cred | Out-Null
    Copy-Item -Path "{payload_path}" -Destination "RemoteTarget:\\{filename}" -Force
    Remove-PSDrive -Name "RemoteTarget"

    # Execute and cleanup
    Invoke-Command -ComputerName {ip} -Credential $cred -ScriptBlock {{
        param($f)
        $proc = Start-Process -FilePath "C:\\Windows\\Temp\\$f" -Wait -PassThru -NoNewWindow
        Remove-Item "C:\\Windows\\Temp\\$f" -Force -ErrorAction SilentlyContinue
        return $proc.ExitCode
    }} -ArgumentList "{filename}"
    """
    try:
        res = subprocess.run(["powershell", "-Command", ps_command], capture_output=True, text=True, timeout=45)
        if res.returncode == 0:
            return {"ip": ip, "status": "SUCCESS", "detail": f"Exit Code: {res.stdout.strip()}"}
        return {"ip": ip, "status": "FAILED", "detail": res.stderr.strip()}
    except Exception as e:
        return {"ip": ip, "status": "ERROR", "detail": str(e)}

# ---------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------
@app.post("/api/run-local")
async def run_local(payload: dict):
    script_content = payload.get("script", "")
    language = payload.get("language", "powershell")
    temp_id = str(uuid.uuid4())[:8]

    if language == "powershell":
        temp_file = os.path.join(BUILD_DIR, f"test_{temp_id}.ps1")
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(script_content)
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", temp_file]
    elif language == "python":
        temp_file = os.path.join(BUILD_DIR, f"test_{temp_id}.py")
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(script_content)
        cmd = [sys.executable, temp_file]
    elif language == "batch":
        temp_file = os.path.join(BUILD_DIR, f"test_{temp_id}.bat")
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(script_content)
        cmd = [temp_file]
    else:
        return JSONResponse({"status": "ERROR", "output": "Unsupported language"}, status_code=400)

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        output = res.stdout if res.returncode == 0 else f"{res.stdout}\n[ERROR]\n{res.stderr}"
    except Exception as e:
        output = f"Execution failed: {str(e)}"
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)

    return {"output": output}

@app.post("/api/compile")
async def compile_script(payload: dict):
    script_content = payload.get("script", "")
    language = payload.get("language", "powershell")
    app_name = payload.get("name", "DeployTool").replace(" ", "_")
    target_id = str(uuid.uuid4())[:6]
    
    if language == "python":
        source_path = os.path.join(BUILD_DIR, f"{app_name}_{target_id}.py")
        with open(source_path, "w", encoding="utf-8") as f:
            f.write(script_content)
        
        cmd = f"pyinstaller --onefile --noconsole --distpath \"{BUILD_DIR}\" --workpath \"{BUILD_DIR}\\work\" \"{source_path}\""
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        exe_path = os.path.join(BUILD_DIR, f"{app_name}_{target_id}.exe")
        
        if os.path.exists(exe_path):
            return {"status": "SUCCESS", "binary_path": exe_path, "log": "Compiled successfully via PyInstaller."}
        return JSONResponse({"status": "FAILED", "log": res.stderr}, status_code=500)

    elif language == "powershell":
        source_path = os.path.join(BUILD_DIR, f"{app_name}_{target_id}.ps1")
        exe_path = os.path.join(BUILD_DIR, f"{app_name}_{target_id}.exe")
        with open(source_path, "w", encoding="utf-8") as f:
            f.write(script_content)

        ps_compile_cmd = f"powershell -Command \"Invoke-PS2EXE -InputFile '{source_path}' -OutputFile '{exe_path}' -noConsole\""
        res = subprocess.run(ps_compile_cmd, shell=True, capture_output=True, text=True)

        if os.path.exists(exe_path):
            return {"status": "SUCCESS", "binary_path": exe_path, "log": "Compiled to standalone EXE."}
        else:
            return {"status": "SUCCESS", "binary_path": source_path, "log": "Staged as raw .ps1 payload."}

    return JSONResponse({"status": "ERROR", "log": "Unsupported compilation target."}, status_code=400)

@app.post("/api/deploy")
async def deploy_fleet(payload: dict):
    binary_path = payload.get("binary_path")
    subnet = payload.get("subnet")
    user = payload.get("domain_user")
    password = payload.get("domain_password")

    if not os.path.exists(binary_path):
        return JSONResponse({"status": "ERROR", "message": "Binary not found. Please compile first."}, status_code=400)

    results = []
    try:
        net = ipaddress.ip_network(subnet, strict=False)
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(deploy_to_single_pc, str(host), binary_path, user, password)
                for host in net.hosts()
            ]
            for f in futures:
                results.append(f.result())
    except Exception as e:
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)

    return {"status": "COMPLETED", "results": results}

# ---------------------------------------------------------
# Embedded Monaco Editor Frontend
# ---------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_studio():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>WinScript Studio</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.36.1/min/vs/loader.min.js"></script>
    </head>
    <body class="bg-gray-900 text-gray-100 flex flex-col h-screen overflow-hidden font-sans select-none">
        
        <header class="bg-gray-800 border-b border-gray-700 px-4 py-2 flex items-center justify-between">
            <div class="flex items-center space-x-3">
                <span class="text-blue-400 font-bold text-lg">⚡ WinScript Studio</span>
                <select id="langSelect" class="bg-gray-700 text-xs px-2 py-1 rounded border border-gray-600 outline-none">
                    <option value="powershell">PowerShell (.ps1)</option>
                    <option value="python">Python (.py)</option>
                    <option value="batch">Batch (.bat)</option>
                </select>
            </div>
            <div class="flex items-center space-x-2">
                <button onclick="runLocal()" class="bg-emerald-600 hover:bg-emerald-500 text-xs font-semibold px-3 py-1.5 rounded transition flex items-center">
                    ▶ Run Test
                </button>
                <button onclick="compileScript()" class="bg-indigo-600 hover:bg-indigo-500 text-xs font-semibold px-3 py-1.5 rounded transition flex items-center">
                    ⚙ Compile Binary
                </button>
            </div>
        </header>

        <div class="flex flex-1 overflow-hidden">
            <div class="w-7/12 flex flex-col border-r border-gray-700">
                <div id="editorContainer" class="flex-1"></div>
            </div>

            <div class="w-5/12 flex flex-col bg-gray-900">
                <div class="p-4 border-b border-gray-800 bg-gray-850">
                    <h2 class="text-xs uppercase font-bold text-gray-400 mb-3 tracking-wider">Fleet Subnet Orchestrator</h2>
                    <div class="grid grid-cols-2 gap-2 text-xs">
                        <div>
                            <label class="block text-gray-400 mb-1">Target Subnet</label>
                            <input id="targetSubnet" type="text" value="192.168.1.0/24" class="w-full bg-gray-800 border border-gray-700 p-1.5 rounded focus:border-blue-500 outline-none">
                        </div>
                        <div>
                            <label class="block text-gray-400 mb-1">Domain User</label>
                            <input id="domainUser" type="text" placeholder="DOMAIN\\Admin" class="w-full bg-gray-800 border border-gray-700 p-1.5 rounded focus:border-blue-500 outline-none">
                        </div>
                        <div class="col-span-2">
                            <label class="block text-gray-400 mb-1">Domain Password</label>
                            <input id="domainPass" type="password" placeholder="••••••••••••" class="w-full bg-gray-800 border border-gray-700 p-1.5 rounded focus:border-blue-500 outline-none">
                        </div>
                    </div>
                    
                    <div class="mt-3 flex items-center justify-between">
                        <span id="binaryStatus" class="text-xs text-gray-400 italic">No binary compiled yet.</span>
                        <button id="deployBtn" onclick="deployFleet()" disabled class="bg-blue-600 disabled:opacity-40 hover:bg-blue-500 text-xs font-semibold px-4 py-1.5 rounded transition">
                            🚀 Push to Domain Subnet
                        </button>
                    </div>
                </div>

                <div class="flex-1 flex flex-col overflow-hidden">
                    <div class="bg-gray-800 px-4 py-1.5 border-b border-gray-700 text-xs font-mono text-gray-300">
                        Output Terminal / Execution Results
                    </div>
                    <pre id="outputLog" class="flex-1 p-4 font-mono text-xs text-emerald-400 bg-black overflow-y-auto whitespace-pre-wrap"></pre>
                </div>
            </div>
        </div>

        <script>
            let editor;
            let compiledBinaryPath = "";

            require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.36.1/min/vs' }});
            require(['vs/editor/editor.main'], function() {
                editor = monaco.editor.create(document.getElementById('editorContainer'), {
                    value: [
                        '# Windows Automation / Remediation Script',
                        'Write-Host "Checking Windows Update Service..." -ForegroundColor Cyan',
                        '$svc = Get-Service -Name "wuauserv"',
                        'if ($svc.Status -ne "Running") {',
                        '    Start-Service -Name "wuauserv"',
                        '    Write-Host "Windows Update Service started successfully." -ForegroundColor Green',
                        '} else {',
                        '    Write-Host "Windows Update Service is healthy." -ForegroundColor Green',
                        '}'
                    ].join('\\n'),
                    language: 'powershell',
                    theme: 'vs-dark',
                    automaticLayout: true,
                    fontSize: 13
                });
            });

            document.getElementById("langSelect").addEventListener("change", (e) => {
                const lang = e.target.value;
                const monacoLang = lang === 'powershell' ? 'powershell' : (lang === 'python' ? 'python' : 'bat');
                monaco.editor.setModelLanguage(editor.getModel(), monacoLang);
            });

            async function runLocal() {
                const log = document.getElementById("outputLog");
                log.innerText = "[*] Running script in local test runner...\\n";
                const res = await fetch("/api/run-local", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        script: editor.getValue(),
                        language: document.getElementById("langSelect").value
                    })
                });
                const data = await res.json();
                log.innerText += data.output;
            }

            async function compileScript() {
                const log = document.getElementById("outputLog");
                log.innerText = "[*] Starting binary compilation pipeline...\\n";
                const res = await fetch("/api/compile", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        script: editor.getValue(),
                        language: document.getElementById("langSelect").value,
                        name: "RemediationTool"
                    })
                });
                const data = await res.json();
                if (data.status === "SUCCESS") {
                    compiledBinaryPath = data.binary_path;
                    document.getElementById("binaryStatus").innerText = "Ready: " + data.binary_path.split('\\\\').pop();
                    document.getElementById("binaryStatus").classList.remove("text-gray-400");
                    document.getElementById("binaryStatus").classList.add("text-emerald-400");
                    document.getElementById("deployBtn").removeAttribute("disabled");
                    log.innerText += "[+] Binary Compiled successfully: " + data.binary_path;
                } else {
                    log.innerText += "[!] Compilation failed:\\n" + data.log;
                }
            }

            async function deployFleet() {
                const log = document.getElementById("outputLog");
                log.innerText += "\\n[*] Initiating subnet scan & deployment...\\n";
                const res = await fetch("/api/deploy", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        binary_path: compiledBinaryPath,
                        subnet: document.getElementById("targetSubnet").value,
                        domain_user: document.getElementById("domainUser").value,
                        domain_password: document.getElementById("domainPass").value
                    })
                });
                const data = await res.json();
                if (data.results) {
                    data.results.forEach(r => {
                        log.innerText += `[${r.status}] ${r.ip} -> ${r.detail}\\n`;
                    });
                } else {
                    log.innerText += "[!] Deployment Error: " + data.message;
                }
            }
        </script>
    </body>
    </html>
    """

def start_backend():
    uvicorn.run(app, host="127.0.0.1", port=9000, log_level="warning")

if __name__ == "__main__":
    # Start backend server in a background thread
    server_thread = threading.Thread(target=start_backend, daemon=True)
    server_thread.start()

    # Launch native desktop window (Edge WebView2)
    webview.create_window(
        title="WinScript Studio - Desktop Orchestration Console",
        url="http://127.0.0.1:9000",
        width=1280,
        height=800,
        resizable=True,
        min_size=(900, 600)
    )
    webview.start()