@echo off
rem This script is a wrapper to start PIIDIgger using the embedded Python binaries in the bin\ directory
rem %* will pass all command line arguments to PIIDigger for processing
bin\python.exe piidigger.py %*