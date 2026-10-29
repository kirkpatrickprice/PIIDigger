<#
.SYNOPSIS
    A Windows PowerShell script that packages PIIDigger with Embedded Python as available from python.org
.DESCRIPTION
    This script packages PIIDigger with the Python embedded environment from https://python.org

    NOTE: This script is signed by KirkpatrickPrice using an Authenticode signature.  Use "Get-AuthenticodeSignature <script-name>" to confirm the validity of the signature.

.PARAMETER pyVersion
    Python version to download

.PARAMETER arch
    Windows architecture (win32, amd64) of Python to download.  Reference https://www.python.org/downloads/windows/ for valid values.  Defaults to amd64.

.PARAMETER venv
    Path to the Python Virtual Environment to use.  Relative to the current directory.

.EXAMPLE
    Default run with PyVersion and venv
    
    .\build_windows_embedded.ps1 -PyVersion="3.12.6" -venv ".venv"

.EXAMPLE
    Use win32 instead of 64-bit

    .\build_windows_embedded.ps1 -PyVersion "3.12.6" -arch "win32"

.LINK
    https://github.com/kirkpatrickprice/PIIDigger

.NOTES
    Author: Randy Bartels
    Official location:  https://github.com/kirkpatrickprice/PIIDigger
    Bug reports:        https://github.com/kirkpatrickprice/PIIDigger/issues 
#>

param(
    [Parameter(Mandatory)]
    [string]$arch = "amd64",
    [Parameter(Mandatory)]
    [string]$pyVersion,
    [Parameter(Mandatory)]
    [string]$venv
)

#Python Embedded download URL
$url = "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-$arch.zip"

# Root directory (current working directory)
$base_dir=(Get-Location).Path

# Destination directories used for copying files
$build_dir = $base_dir + "\dist\piidigger-"+$arch
$bin_dir = $build_dir + "\bin"

# Source directories to copy files from 
$src_dir=$base_dir+"\src"
$venv_dir=$base_dir+"\"+$venv
$site_packages_dir=$venv_dir+"\lib\site-packages\*"

# Additional wrapper files needed to run PIIDigger using embedded Python
$cmd_path = $base_dir + "\piidigger.cmd"
$py_wrapper_path = $base_dir + "\piidigger.py"

function Write-Status {
    param ([string]$text)

    $FG_COLOR = "red"
    Write-Host $text -ForegroundColor $FG_COLOR
}

try {
    Invoke-WebRequest -Uri $url -OutFile python.zip
}
catch {
    $msg = "Failed to download embedded Python $pyVersion ($arch) from $url : $($_.Exception.Message.TrimEnd('.')).  " +
           "Check that the version and architecture exist at https://www.python.org/downloads/windows/ " +
           "(a newly released patch version may not have its embeddable package posted yet)."
    write-status $msg
    # Surface the failure as an annotation on the GitHub Actions run summary
    if ($env:GITHUB_ACTIONS -eq "true") { Write-Host "::error title=Embedded Python download failed::$msg" }
    exit 1
}

# Create the new working folder and all folders leading up to it
Write-Host "Creating new directory $bin_dir"
New-Item -ItemType Directory -Path $bin_dir -Force

# Expand the embedded Python
Write-Host "Expanding python.zip to $bin_dir"
Expand-Archive -Path python.zip -DestinationPath $bin_dir

# Copy over the necessary PiiDigger elements
Write-Host "Copying Site Packages: $site_packages_dir"
    # __editable__* files point back at the build machine's src\ directory
    Copy-Item -Path $site_packages_dir -Destination $bin_dir -Recurse -Exclude *__pycache__*, __editable__*

# Embedded Python's ._pth disables site, so pywin32.pth is never processed.  Add its paths here
# instead; piidigger.py imports pywin32_bootstrap to register the pywin32_system32 DLLs.
$pth_path = (Get-ChildItem -Path $bin_dir -Filter "python3*._pth").FullName
Write-Host "Adding pywin32 paths to $pth_path"
    Add-Content -Path $pth_path -Value "win32", "win32\lib", "pythonwin" -Encoding ascii

Write-Host "Verifying pywin32 loads under embedded Python"
    & "$bin_dir\python.exe" -c "import sys, pywin32_bootstrap, win32api, pythoncom; win32api.GetFileAttributes(sys.executable)"
    if ($LASTEXITCODE -ne 0) {
        write-status "pywin32 failed to load under embedded Python.  OneDrive/Dropbox placeholder detection would be disabled."
        exit 1
    }

Write-Host "Copying Source Files: $src_dir"
    Copy-Item -Path $src_dir -Destination $build_dir -Recurse

write-host "Copying wrapper files: $cmd_path $cmd_path"
    Copy-Item -Path $py_wrapper_path, $cmd_path -Destination $build_dir

# SIG # Begin signature block
# MIIfhAYJKoZIhvcNAQcCoIIfdTCCH3ECAQExDzANBglghkgBZQMEAgEFADB5Bgor
# BgEEAYI3AgEEoGswaTA0BgorBgEEAYI3AgEeMCYCAwEAAAQQH8w7YFlLCE63JNLG
# KX7zUQIBAAIBAAIBAAIBAAIBADAxMA0GCWCGSAFlAwQCAQUABCD6a8XJDzcLkGTW
# eULqNe9VeBR0t203TUQC5Go6zjyd9aCCDQowggZyMIIEWqADAgECAghkM1HTxzif
# CDANBgkqhkiG9w0BAQsFADB8MQswCQYDVQQGEwJVUzEOMAwGA1UECAwFVGV4YXMx
# EDAOBgNVBAcMB0hvdXN0b24xGDAWBgNVBAoMD1NTTCBDb3Jwb3JhdGlvbjExMC8G
# A1UEAwwoU1NMLmNvbSBSb290IENlcnRpZmljYXRpb24gQXV0aG9yaXR5IFJTQTAe
# Fw0xNjA2MjQyMDQ0MzBaFw0zMTA2MjQyMDQ0MzBaMHgxCzAJBgNVBAYTAlVTMQ4w
# DAYDVQQIDAVUZXhhczEQMA4GA1UEBwwHSG91c3RvbjERMA8GA1UECgwIU1NMIENv
# cnAxNDAyBgNVBAMMK1NTTC5jb20gQ29kZSBTaWduaW5nIEludGVybWVkaWF0ZSBD
# QSBSU0EgUjEwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQCfgxNzqrDG
# bSHL24t6h3TQcdyOl3Ka5LuINLTdgAPGL0WkdJq/Hg9Q6p5tePOf+lEmqT2d0bKU
# Vz77OYkbkStW72fL5gvjDjmMxjX0jD3dJekBrBdCfVgWQNz51ShEHZVkMGE6ZPKX
# 13NMfXsjAm3zdetVPW+qLcSvvnSsXf5qtvzqXHnpD0OctVIFD+8+sbGP0EmtpuNC
# GVQ/8y8Ooct8/hP5IznaJRy4PgBKOm8yMDdkHseudQfYVdIYyQ6KvKNc8HwKp4WB
# wg6vj5lc02AlvINaaRwlE81y9eucgJvcLGfE3ckJmNVz68Qho+Uyjj4vUpjGYDdk
# jLJvSlRyGMwnh/rNdaJjIUy1PWT9K6abVa8mTGC0uVz+q0O9rdATZlAfC9KJpv/X
# gAbxwxECMzNhF/dWH44vO2jnFfF3VkopngPawismYTJboFblSSmNNqf1x1KiVgMg
# Lzh4gL32Bq5BNMuURb2bx4kYHwu6/6muakCZE93vUN8BuvIE1tAx3zQ4XldbyDge
# VtSsSKbt//m4wTvtwiS+RGCnd83VPZhZtEPqqmB9zcLlL/Hr9dQg1Zc0bl0EawUR
# 0tOSjAknRO1PNTFGfnQZBWLsiePqI3CY5NEv1IoTGEaTZeVYc9NMPSd6Ij/D+KNV
# t/nmh4LsRR7Fbjp8sU65q2j3m2PVkUG8qQIDAQABo4H7MIH4MA8GA1UdEwEB/wQF
# MAMBAf8wHwYDVR0jBBgwFoAU3QQJB6L1en1SUxKSle44gCUNplkwMAYIKwYBBQUH
# AQEEJDAiMCAGCCsGAQUFBzABhhRodHRwOi8vb2NzcHMuc3NsLmNvbTARBgNVHSAE
# CjAIMAYGBFUdIAAwEwYDVR0lBAwwCgYIKwYBBQUHAwMwOwYDVR0fBDQwMjAwoC6g
# LIYqaHR0cDovL2NybHMuc3NsLmNvbS9zc2wuY29tLXJzYS1Sb290Q0EuY3JsMB0G
# A1UdDgQWBBRUwv4QlQCTzWr158DX2bJLuI8M4zAOBgNVHQ8BAf8EBAMCAYYwDQYJ
# KoZIhvcNAQELBQADggIBAPUPJodwr5miyvXWyfCNZj05gtOII9iCv49UhCe204MH
# 154niU2EjlTRIO5gQ9tXQjzHsJX2vszqoz2OTwbGK1mGf+tzG8rlQCbgPW/M9r1x
# xs19DiBAOdYF0q+UCL9/wlG3K7V7gyHwY9rlnOFpLnUdTsthHvWlM98CnRXZ7WmT
# V7pGRS6AvGW+5xI+3kf/kJwQrfZWsqTU+tb8LryXIbN2g9KR+gZQ0bGAKID+260P
# Z+34fdzZcFt6umi1s0pmF4/n8OdX3Wn+vF7h1YyfE7uVmhX7eSuF1W0+Z0duGwdc
# +1RFDxYRLhHDsLy1bhwzV5Qe/kI0Ro4xUE7bM1eV+jjk5hLbq1guRbfZIsr0WkdJ
# LCjoT4xCPGRo6eZDrBmRqccTgl/8cQo3t51Qezxd96JSgjXktefTCm9r/o35pNfV
# HUvnfWII+NnXrJlJ27WEQRQu9i5gl1NLmv7xiHp0up516eDap8nMLDt7TAp4z5T3
# NmC2gzyKVMtODWgqlBF1JhTqIDfM63kXdlV4cW3iSTgzN9vkbFnHI2LmvM4uVEv9
# XgMqyN0eS3FE0HU+MWJliymm7STheh2ENH+kF3y0rH0/NVjLw78a3Z9UVm1F5VPz
# iIorMaPKPlDRADTsJwjDZ8Zc6Gi/zy4WZbg8Zv87spWrmo2dzJTw7XhQf+xkR6Od
# MIIGkDCCBHigAwIBAgIQMGhaa9kq6JTdsNvho6ILEDANBgkqhkiG9w0BAQsFADB4
# MQswCQYDVQQGEwJVUzEOMAwGA1UECAwFVGV4YXMxEDAOBgNVBAcMB0hvdXN0b24x
# ETAPBgNVBAoMCFNTTCBDb3JwMTQwMgYDVQQDDCtTU0wuY29tIENvZGUgU2lnbmlu
# ZyBJbnRlcm1lZGlhdGUgQ0EgUlNBIFIxMB4XDTI1MTIxNTIwMzgxNVoXDTI3MTIy
# NjIwMzgxNVowdzELMAkGA1UEBhMCVVMxEjAQBgNVBAgMCVRlbm5lc3NlZTESMBAG
# A1UEBwwJTmFzaHZpbGxlMR8wHQYDVQQKDBZLaXJrcGF0cmljayBQcmljZSBJbmMu
# MR8wHQYDVQQDDBZLaXJrcGF0cmljayBQcmljZSBJbmMuMIIBojANBgkqhkiG9w0B
# AQEFAAOCAY8AMIIBigKCAYEAh+DGWDWaY5ki9IwXF8Ddmv0o+kODNKLmtekGG3Vt
# cwSvQsOJlChDJqN+hAGiWTsr0gHVFGo9gOYOdH28HmETqm3hQvS/KZaUuPmy3hj0
# zhKtX5edFcRzwpqxTgEodZi4byBnMWZiiq/qeLICaqX72Skj2JBsNxKAKCesNh2W
# +5Go63SqqGmTtiG12FVo0B8IdhIxV9z8LdozUG5kCsEFWIJROURGNXZNOT27b7mA
# /M/yRyJkFG4e1GP/bwmxVKBPgpNSeDFJTvgiTZr/1zF2DqNqAjfR2PYVHPE/9V7B
# 9imse2ovp3XQKjx+5u5wBLbhKkKguPxUqBuBSNdG72wgEFDgEEcdLvvMYwoCffoa
# t2SQpFhsPlrdGr+NG/36SRrRpZTw3XQc/2OlCTQm1CZPE5NP4dek7Mfwg0h4L26v
# euWeeLBC/I+/fEA4AyL9O//mpksIbyAL1myXbA6TEnVr8bRooMZKjGeMYdVDjTgr
# Cs0ZJYZxYonSdarrAIN95onzAgMBAAGjggGVMIIBkTAMBgNVHRMBAf8EAjAAMB8G
# A1UdIwQYMBaAFFTC/hCVAJPNavXnwNfZsku4jwzjMHoGCCsGAQUFBwEBBG4wbDBI
# BggrBgEFBQcwAoY8aHR0cDovL2NlcnQuc3NsLmNvbS9TU0xjb20tU3ViQ0EtQ29k
# ZVNpZ25pbmctUlNBLTQwOTYtUjEuY2VyMCAGCCsGAQUFBzABhhRodHRwOi8vb2Nz
# cHMuc3NsLmNvbTBRBgNVHSAESjBIMAgGBmeBDAEEATA8BgwrBgEEAYKpMAEDAwEw
# LDAqBggrBgEFBQcCARYeaHR0cHM6Ly93d3cuc3NsLmNvbS9yZXBvc2l0b3J5MBMG
# A1UdJQQMMAoGCCsGAQUFBwMDME0GA1UdHwRGMEQwQqBAoD6GPGh0dHA6Ly9jcmxz
# LnNzbC5jb20vU1NMY29tLVN1YkNBLUNvZGVTaWduaW5nLVJTQS00MDk2LVIxLmNy
# bDAdBgNVHQ4EFgQUegdXMG3gSLgnoEqIXRd18nk7lDswDgYDVR0PAQH/BAQDAgeA
# MA0GCSqGSIb3DQEBCwUAA4ICAQAah5BOjtcTWGp2HNJuksUmt5YGM4oPElaP16n6
# +FTotluUgc4lzpBuDAOGCzNN0XRn5fBLfVKsLpE73dUU+g5QzZptZgcRgqHyv657
# lu1GrJPhbePboAWEBCZVR6tcm5ARALXh9FdBEiC909Weew8O9c12JtyXyhsLVjV7
# 2O5Xu3Oh2MGeHRE+UffwnBF6yBaKQHzY4G2BQS4UVRoC/hGvUGWkNi1p/pc3mIXb
# 2wenSsYm632jT2BPsVVHst6jRhHDJ6wpS4dhNLukpWDp/gi1leNQEcpCAhBOq+sd
# 19SxzaC55Z+zcnQPzNqrnOkPzAjJKA7N3OeDsto6RK3RyJZ+ErE59UO04iwryrWO
# jNDqntMQTJB8kuZ24Ar1X/H4ylpUkV2jybTcp4DrYaq9ECh0XQ29MFi4t0iCBcX2
# eYYFmgHauDJOJFO8EpAFNSOimqeyx+sIszE/gFAmVl+Fpb1TmmauaSMSS/0jSrlu
# FqlkSsRDwpFP8BB+DhynDYvYwVNp9PUu/gG7BVYqDmmyHO7uE4u4waMVZn7pztNF
# 5MovpAYb8pcyFUncG+jS/jSAsVIw0E0FxB/W7ZVSdatlAFfXfxlSTU4XFmcmDXWh
# Bne8dBeJRQw5GTyLUoYOxzg3FOXDhgtfWTD/GGq8uP4nTghptW99llbNHZbTOCgO
# sFrJZjGCEdAwghHMAgEBMIGMMHgxCzAJBgNVBAYTAlVTMQ4wDAYDVQQIDAVUZXhh
# czEQMA4GA1UEBwwHSG91c3RvbjERMA8GA1UECgwIU1NMIENvcnAxNDAyBgNVBAMM
# K1NTTC5jb20gQ29kZSBTaWduaW5nIEludGVybWVkaWF0ZSBDQSBSU0EgUjECEDBo
# WmvZKuiU3bDb4aOiCxAwDQYJYIZIAWUDBAIBBQCgfDAQBgorBgEEAYI3AgEMMQIw
# ADAZBgkqhkiG9w0BCQMxDAYKKwYBBAGCNwIBBDAcBgorBgEEAYI3AgELMQ4wDAYK
# KwYBBAGCNwIBFTAvBgkqhkiG9w0BCQQxIgQgFNdzNDzUSDi44VQwI9D0fboXBYh2
# EcyfStccZShfJjIwDQYJKoZIhvcNAQEBBQAEggGASkcRhUwp5Zb8abAIw8dpSXyk
# PpreizQjjKI7tIut7f9dOnWx5S39NrOMndzO6UINARXSxW0BzCYot8ekgQnJX4V1
# HptCxizUUPOyVimpPz9Ar1e1g1VESUOdOdHuyJZfcBOs/CzPUjp2jc3BKCQ2VOsC
# Ty7HIFqSZF5EStUeJx4Tyby63i0EIy78xkAoGnMXAHkNRgHVyISF6dsMinTPA/g2
# dN5/IE/CPRF5qgD0vIEigsDtu4yD/2+9Ze7TCSsLBRhney+dE1dlEwOKIBwShVn3
# PAdwJgT3IP8Kg6nlKt1tCyhMKOzRNrbxbXXqDRmn0F24v0SFBJUjqncee4GaUkRr
# pUYVf0+kbZ0z1Cc1nGyYBXssgT2dHcD3ljZjfiyfQ6hA/k0RNveiu17QWUlMja5q
# hfmckbXlQRuWxNUKLNjcfn4yG0fhS58bIkYRjUsXjq+R01vQvWV0y7xFLFC1RzTs
# QGXJ2HsUfZXVw+4nUcDAm0ZgAu3zRcnfYTGv/IjkoYIPFjCCDxIGCisGAQQBgjcD
# AwExgg8CMIIO/gYJKoZIhvcNAQcCoIIO7zCCDusCAQMxDTALBglghkgBZQMEAgEw
# dwYLKoZIhvcNAQkQAQSgaARmMGQCAQEGDCsGAQQBgqkwAQMGATAxMA0GCWCGSAFl
# AwQCAQUABCDwe7TDf8MsBQMAKofXulxAvhkfodH5wsP9qR4ab35tcQIIN+k0JkLS
# BEcYDzIwMjYxMDI4MjEyNzE3WjADAgEBoIIMADCCBPwwggLkoAMCAQICEB9rFmLS
# LhtsXfHAwaBgDjgwDQYJKoZIhvcNAQELBQAwczELMAkGA1UEBhMCVVMxDjAMBgNV
# BAgMBVRleGFzMRAwDgYDVQQHDAdIb3VzdG9uMREwDwYDVQQKDAhTU0wgQ29ycDEv
# MC0GA1UEAwwmU1NMLmNvbSBUaW1lc3RhbXBpbmcgSXNzdWluZyBSU0EgQ0EgUjEw
# HhcNMjUwMjE4MTYzMjAyWhcNMzQxMTEyMTg1MDA1WjBuMQswCQYDVQQGEwJVUzEO
# MAwGA1UECAwFVGV4YXMxEDAOBgNVBAcMB0hvdXN0b24xETAPBgNVBAoMCFNTTCBD
# b3JwMSowKAYDVQQDDCFTU0wuY29tIFRpbWVzdGFtcGluZyBVbml0IDIwMjUgRTEw
# WTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQb+1FRx2/mVZbsT15XxNzOrYNF56vH
# YnPjt7zzgkaWpVDNbzvQPhkc0lVY8soB4WKwcOtlyxYdZL+3vskXf2k2o4IBWjCC
# AVYwHwYDVR0jBBgwFoAUDJ0QJY6apxuZh0PPCH7hvYGQ9M8wUQYIKwYBBQUHAQEE
# RTBDMEEGCCsGAQUFBzAChjVodHRwOi8vY2VydC5zc2wuY29tL1NTTC5jb20tdGlt
# ZVN0YW1waW5nLUktUlNBLVIxLmNlcjBRBgNVHSAESjBIMDwGDCsGAQQBgqkwAQMG
# ATAsMCoGCCsGAQUFBwIBFh5odHRwczovL3d3dy5zc2wuY29tL3JlcG9zaXRvcnkw
# CAYGZ4EMAQQCMBYGA1UdJQEB/wQMMAoGCCsGAQUFBwMIMEYGA1UdHwQ/MD0wO6A5
# oDeGNWh0dHA6Ly9jcmxzLnNzbC5jb20vU1NMLmNvbS10aW1lU3RhbXBpbmctSS1S
# U0EtUjEuY3JsMB0GA1UdDgQWBBTOfNnABIDFJBBqCeocqyU9Ef2o0jAOBgNVHQ8B
# Af8EBAMCB4AwDQYJKoZIhvcNAQELBQADggIBAIBzdSSFvWZTYqdOxX2Pa9SycCyk
# 5dYf8va9QYw4BzBM+1Oye5gMt9icQNSi8owgiajZLkX4QxxhRH2bY0zq7uwEDA0g
# 4lA7wgnN42s+acvYxr8CHuZ6ZC216l4QPkqgsxu9fVEooMIR5GK+7/DYl6GmNF7t
# N6dAYmWa3mZaZ4JhgffvOjfeBaiBcdiOWeYrru3eI+2gDmQTcX1PYxQyCBg7Vn1n
# gqRdEPKyxP9wxw5cySrOSJd4v1aDpe601Pd/KEq8TMMMOhgwZSl/Kxh8b5I4CG/T
# F5FYJSUgx7DQGQ3gkv4p2sShwPgobgfCuDsSMWupNXvnhwcqWpAGN8Lje8Oz5T5o
# TepOSHamZ9x2R7NlY6EfONWfcJlTxXRg3ZAWe1zpm+TmVGirn7L7REdFW2Ng5M2q
# gGiLbdZHPiPhVM+z4lc9j1M6XoJH7UUcb6z8iC9PG5SU52zENF66gzdKxH7ky7+F
# 4YZysiWl4cQlHYzv3EbpjM2d+UE1DCLQVstcRqdIsqBVZwMmtqT7FTLgCPrMtc8Z
# TkvZeYksbyifIKZ6ToI6mE2u7tXsmaPuCZXBqIokdey7YTE6xdautrTucTqSPLcV
# GeoLShrZCVesHY/r2S3MCmdjPdxqlaTqOlAZRo8dbCrFWewCFMyWZYuDSdDNimHK
# 3zZ5Xm5GCqQ50qlsMIIG/DCCBOSgAwIBAgIQbVIYcIfoI02FYADQgI+TVjANBgkq
# hkiG9w0BAQsFADB8MQswCQYDVQQGEwJVUzEOMAwGA1UECAwFVGV4YXMxEDAOBgNV
# BAcMB0hvdXN0b24xGDAWBgNVBAoMD1NTTCBDb3Jwb3JhdGlvbjExMC8GA1UEAwwo
# U1NMLmNvbSBSb290IENlcnRpZmljYXRpb24gQXV0aG9yaXR5IFJTQTAeFw0xOTEx
# MTMxODUwMDVaFw0zNDExMTIxODUwMDVaMHMxCzAJBgNVBAYTAlVTMQ4wDAYDVQQI
# DAVUZXhhczEQMA4GA1UEBwwHSG91c3RvbjERMA8GA1UECgwIU1NMIENvcnAxLzAt
# BgNVBAMMJlNTTC5jb20gVGltZXN0YW1waW5nIElzc3VpbmcgUlNBIENBIFIxMIIC
# IjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEArlEQE9L5PCCgIIXeyVAcZMnh
# /cXpNP8KfzFI6HJaxV6oYf3xh/dRXPu35tDBwhOwPsJjoqgY/Tg6yQGBqt65t94w
# px0rAgTVgEGMqGri6vCI6rEtSZVy9vagzTDHcGfFDc0Eu71mTAyeNCUhjaYTBkyA
# Nqp9m6IRrYEXOKdd/eREsqVDmhryd7dBTS9wbipm+mHLTHEFBdrKqKDM3fPYdBOr
# o3bwQ6OmcDZ1qMY+2Jn1o0l4N9wORrmPcpuEGTOThFYKPHm8/wfoMocgizTYYeDG
# /+MbwkwjFZjWKwb4hoHT2WK8pvGW/OE0Apkrl9CZSy2ulitWjuqpcCEm2/W1RofO
# unpCm5Qv10T9tIALtQo73GHIlIDU6xhYPH/ACYEDzgnNfwgnWiUmMISaUnYXijp0
# IBEoDZmGT4RTguiCmjAFF5OVNbY03BQoBb7wK17SuGswFlDjtWN33ZXSAS+i45My
# 1AmCTZBV6obAVXDzLgdJ1A1ryyXz4prLYyfJReEuhAsVp5VouzhJVcE57dRrUanm
# Pcnb7xi57VPhXnCuw26hw1Hd+ulK3jJEgbc3rwHPWqqGT541TI7xaldaWDo85k4l
# R2bQHPNGwHxXuSy3yczyOg57TcqqG6cE3r0KR6jwzfaqjTvN695GsPAPY/h2YksN
# gF+XBnUD9JBtL4c34AcCAwEAAaOCAYEwggF9MBIGA1UdEwEB/wQIMAYBAf8CAQAw
# HwYDVR0jBBgwFoAU3QQJB6L1en1SUxKSle44gCUNplkwgYMGCCsGAQUFBwEBBHcw
# dTBRBggrBgEFBQcwAoZFaHR0cDovL3d3dy5zc2wuY29tL3JlcG9zaXRvcnkvU1NM
# Y29tUm9vdENlcnRpZmljYXRpb25BdXRob3JpdHlSU0EuY3J0MCAGCCsGAQUFBzAB
# hhRodHRwOi8vb2NzcHMuc3NsLmNvbTA/BgNVHSAEODA2MDQGBFUdIAAwLDAqBggr
# BgEFBQcCARYeaHR0cHM6Ly93d3cuc3NsLmNvbS9yZXBvc2l0b3J5MBMGA1UdJQQM
# MAoGCCsGAQUFBwMIMDsGA1UdHwQ0MDIwMKAuoCyGKmh0dHA6Ly9jcmxzLnNzbC5j
# b20vc3NsLmNvbS1yc2EtUm9vdENBLmNybDAdBgNVHQ4EFgQUDJ0QJY6apxuZh0PP
# CH7hvYGQ9M8wDgYDVR0PAQH/BAQDAgGGMA0GCSqGSIb3DQEBCwUAA4ICAQCSGXUN
# plpCzxkH2fL8lPrAm/AV6USWWi9xM91Q5RN7mZN3D8T7cm1Xy7qmnItFukgdtiUz
# LbQokDJyFTrF1pyLgGw/2hU3FJEywSN8crPsBGo812lyWFgAg0uOwUYw7WJQ1teI
# CycX/Fug0KB94xwxhsvJBiRTpQyhu/2Kyu1Bnx7QQBA1XupcmfhbQrK5O3Q/yIi/
# /kN0OkhQEiS0NlyPPYoRboHWC++wogzV6yNjBbKUBrMFxABqR7mkA0x1Kfy3Ud08
# qyLC5Z86C7JFBrMBfyhfPpKVlIiiTQuKz1rTa8ZW12ERoHRHcfEjI1EwwpZXXK5J
# 5RcW6h7FZq/cZE9kLRZhvnRKtb+X7CCtLx2h61ozDJmifYvuKhiUg9LLWH0Or9D3
# XU+xKRsRnfOuwHWuhWch8G7kEmnTG9CtD9Dgtq+68KgVHtAWjKk2ui1s1iLYAYxn
# Dm13jMZm0KpRM9mLQHBK5Gb4dFgAQwxOFPBslf99hXWgLyYE33vTIi9p0gYqGHv4
# OZh1ElgGsvyKdUUJkAr5hfbDX6pYScJI8v9VNYm1JEyFAV9x4MpskL6kE2Sy8rOq
# S9rQnVnIyPWLi8N9K4GZvPit/Oy+8nFL6q5kN2SZbox5d69YYFe+rN1sDD4CpNWw
# BBTI/q0V4pkgvhL99IV2XasjHZf4peSrHdL4RjGCAlgwggJUAgEBMIGHMHMxCzAJ
# BgNVBAYTAlVTMQ4wDAYDVQQIDAVUZXhhczEQMA4GA1UEBwwHSG91c3RvbjERMA8G
# A1UECgwIU1NMIENvcnAxLzAtBgNVBAMMJlNTTC5jb20gVGltZXN0YW1waW5nIElz
# c3VpbmcgUlNBIENBIFIxAhAfaxZi0i4bbF3xwMGgYA44MAsGCWCGSAFlAwQCAaCC
# AWEwGgYJKoZIhvcNAQkDMQ0GCyqGSIb3DQEJEAEEMBwGCSqGSIb3DQEJBTEPFw0y
# NjEwMjgyMTI3MTdaMCgGCSqGSIb3DQEJNDEbMBkwCwYJYIZIAWUDBAIBoQoGCCqG
# SM49BAMCMC8GCSqGSIb3DQEJBDEiBCB4ryAOTQlR6vHPCKE9oU/gy5c89gWY9Iml
# /6JQ7INdyzCByQYLKoZIhvcNAQkQAi8xgbkwgbYwgbMwgbAEIFQq+aFqjXIuZhFJ
# eIrplMGKmq7lplyzRKJUmvlseceLMIGLMHekdTBzMQswCQYDVQQGEwJVUzEOMAwG
# A1UECAwFVGV4YXMxEDAOBgNVBAcMB0hvdXN0b24xETAPBgNVBAoMCFNTTCBDb3Jw
# MS8wLQYDVQQDDCZTU0wuY29tIFRpbWVzdGFtcGluZyBJc3N1aW5nIFJTQSBDQSBS
# MQIQH2sWYtIuG2xd8cDBoGAOODAKBggqhkjOPQQDAgRHMEUCIBtG00RwFCT6uFdu
# WBb/kSjs0G+oZBjydaXRtjdLY1vhAiEAuky0dLqD/bMA8SihKQ7GDWIC5aJEJY0q
# 06ldu9Ywd7o=
# SIG # End signature block
