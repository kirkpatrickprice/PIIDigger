@echo off
rem This script is a wrapper to start PIIDIgger using the embedded Python binaries in the bin\ directory
rem %~dp0 is this script's own folder, so PIIDigger runs from any folder or from PATH
rem %* will pass all command line arguments to PIIDigger for processing
"%~dp0bin\python.exe" "%~dp0piidigger.py" %*
