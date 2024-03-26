# Windows Binaries
## Some general notes
The ZIP file above contains a standalone, 64-bit version of PIIDigger:
* It does not require any installation.  Just download and unzip it.
* There will be a `piddigger.exe` and an `_internal` folder.  Both the EXE and the folder are required to run. 
* If using PIIDigger on multiple systems
    * You can create a `piidigger.toml` file with your own configuration, for instance, maybe for writing results files to a shared network folder.  
    * You could then repackage PIIDigger into a new ZIP file
    * This configuration file will be used automatically if it exists.

## File Integrity
* Use the Powershell command `Get-FileHash .\PIIDigger.zip` on the downloaded ZIP file to confirm the integrity of the file against the hashes listed above.
* `piidigger.exe` in the ZIP file has also been signed by a code-signing certificate issued by SSL.Com to KirkpatrickPrice.  You can verify this signature by selecting `Properties -> Digital Signatures -> Details" and verifying it reports that "This digital signature is OK."
* DLLs in the `_internal` folder are signed by either Microsoft or Python Software Foundation, which can be verified in the same manner as `piddigger.exe` above.

## Windows Anti-Virus
Defender and other A/V may warn that PIIDigger contains a virus.  Occasionally packaging a Python program results in this false positive.  I've taken steps to avoid this, but if you receive such a warning see the [ERRATA](https://github.com/kirkpatrickprice/PIIDigger/blob/main/ERRATA.md) for workarounds.

## Building PIIDigger from Source

If you require a 32-bit version or would like to build your own executable version, the following steps will create a fresh package PIIDigger:

### TL/DR
Create a build environment by installing a 32-bit version of Python, cloning the PIIDigger repo, installing the dependencies, and running the build script.

### Details
All `typed commands` assume use of Powershell...
1. Install [Python](https://python.org) directly from Python (don't use the Windows Store version).  The latest version of Python 3.12 is recommended.
    * Note: If you require a 32-bit of PIIDigger, use a 32-bit version of Python.

2. Clone the PIIDigger repo from GitHub (install GitHub desktop if you don't have it already installed)
  
    git clone https://github.com/kirkpatrickprice/PIIDigger
    
3. Create a Python virtual environment and install all PIIDigger dependencies
    
    cd PIIDigger
    py -m venv .venv
    .\.venv\Scripts\activate
    pip install -e .[win]

4. Test that PIIDigger runs correctly from native Python before attempting to package an EXE

    py .\piidigger.py --help
    py .\piidigger.py -c testfile.toml
    
3. Install PyInstaller to create the EXE
    
    pip install pyinstaller
    
4. Run the build script without the Code Signing option:
    
    .\build_windows_exe.ps1 -NoCodeSign

5. `PIIDigger.zip` should be in the `build\windows` folder.
