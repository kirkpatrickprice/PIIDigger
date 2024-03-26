# Windows Binaries
## Some general notes
The ZIP file above contains a standalone version of PIIDigger:
* It does not require any installation.  Just download and unzip it.
* There will be a `piddigger.exe` and an `_internal` folder.  Both the EXE and the folder are required to run. 
* If using PIIDigger on multiple systems, you can create a `piidigger.toml` file with your own configuration, for instance, maybe for writing results files to a shared network folder.  This configuration file will be used automatically if it exists.

## File Integrity
* All GitHub commits are signed -- verify the commit by checking for the `Verified` tag after clicking on the latest commit message.
* Use the Powershell command `Get-FileHash` on the downloaded ZIP file to confirm the integrity of the file against the hashes listed above.

## Windows Anti-Virus
Defender and other A/V may warn that PIIDigger contains a virus.  Occasionally packaging a Python program results in this false positive.  I've taken steps to avoid this, but if you receive such a warning see the [ERRATA](https://github.com/kirkpatrickprice/PIIDigger/blob/main/ERRATA.md) for workarounds.