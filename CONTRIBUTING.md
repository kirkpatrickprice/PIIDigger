# Contributing to PIIDigger
YES!!!  Please do!  On a good day, I'm a Python Hack.  So any help anyone wants to throw at it is very much appreciated.  Don't worry about CI tests.  Here's the criteria for merging:
1. Your patch works 
2. Your patch doesn't break stuff on the three supported platforms (Windows, Linux and MacOS)
3. Your patch builds well in Pyinstaller

That's it.

This is an initial release and -- sadly -- I still have a day job.  There's plenty to do:
* Refactor the file content extraction.
* Refactor results output handling
* Ongoing:
    * Add file handler types -- anything that Python can read is open game.
    * Add data handler types -- and the associated Regex tuning.