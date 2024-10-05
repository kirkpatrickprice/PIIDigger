# Windows Releases
## Some general notes
Each release consists of a standalone, 64-bit version of PIIDigger delivered as a ZIP file:
* The file name is `piidigger-<architecture>.zip` where the only `architecture` currently supported `amd64` (testing on `win32` is forthcoming).
* It does not require any installation.  Just download and unzip it.
* There will be `piddigger.cmd` and `piidigger.py` files and `src` and `bin` folders.  All of them are required to run. 
* Either double-click or use PowerShell to run `piidigger.cmd`.
* If using PIIDigger on multiple systems
    * You can create a `piidigger.toml` file with your own configuration, for instance, maybe for writing results files to a shared network folder.  See "Advanced Configuration" on the main [readme](https:\\github.com\kirkpatrickprice\PIIDigger) file for additional information.
    * You could then repackage PIIDigger into a new ZIP file
    * This configuration file will be used automatically if it exists.

## File Integrity
* Use the Powershell command `Get-FileHash .\piidigger-<architecture>.zip` on the downloaded ZIP file to confirm the integrity of the file against the hashes listed on the Releases page.
* Binary and DLL files in the `bin` folder have been signed by either Python Software Foundation or Microsoft.

## Windows Anti-Virus
Compiled Python programs are frequently treated by anti-virus vendors as suspicious.  However, by using Embedded Python directly from Python Software Foundation, we are able to avoid these detections.  Each release of PIIDigger is tested against VirusTotal, but feel free to upload it for yourself.  Be sure to report any negative findings so we can chase them down.

## Building PIIDigger from Source
If you require a 32-bit version or would like to build your own executable version, the following steps will create a fresh package PIIDigger:

### TL/DR
Create a build environment by installing a 32-bit version of Python, cloning the PIIDigger repo, installing the dependencies, and running the build script.

### Details
All `typed commands` assume use of Powershell...
1. Install [Python](https://python.org) directly from Python (don't use the Windows Store version).  The latest version of Python 3.12 is recommended.
    * Note: If you require a 32-bit of PIIDigger, use a 32-bit version of Python.

2. Clone the PIIDigger repo from GitHub (install GitHub desktop if you don't have it already installed)
    ```
    git clone https://github.com/kirkpatrickprice/PIIDigger
    ```
3. Create a Python virtual environment and install all PIIDigger dependencies
    ```
    cd PIIDigger
    py -m venv .venv
    .\.venv\Scripts\activate
    pip install -e .[win]
    ```
4. Test that PIIDigger runs correctly from native Python before attempting to package an EXE
    ```
    py .\piidigger.py --help
    py .\piidigger.py -c testfile.toml
    ```

    The first command should display the help content. The second command should create a file called `testfile.toml` containing a default configuration.  It can be deleted.

3. Run the `build_windows_embedded.ps1` script.
    ```
    .\build_windows_embedded -py_version 3.12.6 -arch win32 -venv .venv
    ```

    `py_version` should match the version of Python you downloaded in step 1
    `arch` should match of the version of Python you download (e.g. amd64 or win32)
    `venv` should match the folder you created for your Python Virtual Environment in step 3
    
4. PIIDigger will be ready to run in the should be in the `dist\piidigger-<arch>` folder.  You can ZIP it, copy it, etc. for distribution to as many computers as you'd like to run it on.
