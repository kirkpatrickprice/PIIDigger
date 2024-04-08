# Performance Tuning Notes

*TL/DR* Use the `-p` command line option to control the number of file scanners, which will affect the CPU and RAM utilization.

A few notes and observations about performance tuning:
* This is a CPU-bound application
* Regex searches are expensive to both setup and to perform
* There's a happy balance between setting up the regex modules and the amount of text that can be efficiently searched
* To manage the balance and keep the CPU busy, we do a few things:
    * The application is built on a pipeline of Directories --> Files --> File Scanner --> Results
    * Each step in the pipeline is managed by a distinct concurrent process.  This ensures that the File Scanner always has work to do.
    * The File Scanner is where all of the hard work happens.
        * We read each file in about 60MB chunks at a time
        * We combine each 60MB-ish block of file data into a string and then break the string up into ideally-sized chunks for Regex to do its thing
        * We only break on a word boundary, so strings won't be chopped up mid-word
        * Long lines without whitespace will be broken up mid-string
    * To get the most out of the available CPU resources, we create a separate file scanning process for each logical CPU core reported by the OS

**Therefore...** the ~~primary~~ *only* mechanism for users to adjust performance is to override the default for concurrent file scanners.  This is done with the `-p` option, such as:

```
piidigger -p 6
```

## Counting Cores (especially on Intel HT CPUs)
*TL/DR* `(logical_cpu_cores / 2) - X` should be the starting point for saving some compute resources for other work.  `X` can be adjusted by exactly how much you want to give to PIIDigger and how much you need left over for other things.

*Longer version...*
Different architectures might report cores differently, but at least for Intel HyperThreaded CPUs, each phyiscal core is further divided into its HT components.  This will cause the OS to see twice as many CPUs as there are physical cores.

Using the default value for file scanning processes will result in 100% CPU utilization for the duration of the scan.  The same is also likely true for any value of `-p ` between "real cores" and "logical cores".  That's OK on an end-user device, but if that's not acceptable for your production servers, be sure to use the `-p` parameter to specify the number of processes to start.

When configuring the program to use fewer processes than there are cores (using the `-p` parameter), you might not see much CPU relief until you use a number less than half of the OS-reported cores.  That is, if the OS sees 16 cores, you won't see much relief until you're below 8.  This /could be/ important for your production servers.

Even when limiting the number of processes, it won't be a perfect linear, pro-rated CPU utilization cap.  For instance, if you have 8 real cores (16 logical Intel HT cores) and use `-p 6` you will not be restricted to exactly 75% utilization.  But you will leave two physical cores for other tasks.

You can see how many CPU cores the application has available by using the `--cpu-count` command line paramter.  For example:

```
piidigger --cpu-count
CPU cores: 16
```

## Chunk Size
For now, the Regex chunk size is hard-coded at 650 bytes.  In testing, this seemed to be the "happy place" at least for the current set of datahandlers.  Maybe there will be a feature to adjust that dynamically based on performance.

The quantity of data read from each file is a function of Regex chunk size.  Currently, the file handlers will read 100,000 Regex chunks (or approx 61MB) of data at a time.  This should ensure that disk IO is not the bottle neck, while hopefully consuming reasonable amounts of RAM.


## RAM Utilization
As a rule, fewer concurrent processes will use less RAM than the default.  However, RAM usage will NOT be a one-to-one correlation with the `-p` value.

There is a special note here about JSON results: Because of the way that JSON results are written to disk, we hold all of the JSON-destined results in RAM until the program is complete and then we write them to disk all at once.  This ensure that JQ or any other use of the JSON results file will see the results as a single list, instead of a bunch of disconnected, one-off JSON records.  This /could/ result in loss of JSON results in the event of an unexpected condition, but in that case, you'll still have the text file results to fall back on.

## Additional Optimizations
Regex optimization is a "fun" topic and I'm nowhere near an expert.  Heck, on most days, I can barely string together a functioning regex to find my own name.  Suggestions to improve regex performance are always welcome, keeping in mind that this is the heart-and-soul of PIIDigger both in terms of reliable detections and performance.