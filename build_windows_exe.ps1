<#
.SYNOPSIS
    A Windows PowerShell script that builds a standalone Windows PIIDigger executable
.DESCRIPTION
    This script packages a Python runtime environment and related PIIDigger application code into a standalone (doesn't need a Python installation) program for distribution.

    NOTE: This script is signed by KirkpatrickPrice using an Authenticode signature.  Use "Get-AuthenticodeSignature .\kpadaudit.ps1" to confirm the validity of the signature.
.PARAMETER NoCodeSign
    Skip the step for signing PIIDigger.exe with the KP code signing certificate.

.EXAMPLE
    Default run without any parameters.
    
    .\build_windows_exe.ps1

.EXAMPLE
    Skipping the code signing step

    .\build_windows_exe.ps1 -NoCodeSign

    This will produce a functioning piidigger.exe, but it will not be digitally signed.

.LINK
    https://github.com/kirkpatrickprice/PIIDigger

.NOTES
    Author: Randy Bartels
    Official location:  https://github.com/kirkpatrickprice/PIIDigger
    Bug reports:        https://github.com/kirkpatrickprice/PIIDigger/issues 
#>

param(
    [switch]$NoCodeSign
)

$base_dir=(Get-Location).Path
$bin_dir = $base_dir + "\dist\windows"
$zip_file = $bin_dir + "\PIIDigger.zip"
$build_dir = $bin_dir + "\piidigger"
$exe_path = $build_dir+"\piidigger.exe"
$piidigger_options="--onedir --distpath $bin_dir -i $base_dir\piidigger.ico --collect-submodules wakepy piidigger.py"
$hashes = @("SHA256", "SHA384", "SHA512")

function sign-file {
    param (
        [string]$file
    )

    $fingerprint = "BB28A0694BF3F3C294F462001D7A8C8CE62AF950"
    $sign_command = "signtool.exe" 
    $sign_options = "sign /fd SHA256 /tr http://ts.ssl.com /td sha256 /sha1 $fingerprint $file"
    try {
        Start-Process -FilePath $sign_command -ArgumentList $sign_options -NoNewWindow -Wait
    }
    catch {Write-Status "Error attemptint to sign $file.  Proceeding without signing."}
}

function Write-Status {
    param ([string]$text)

    $FG_COLOR = "red"
    Write-Host $text -ForegroundColor $FG_COLOR
}

Write-Status "Testing if in a Virtual Environment"
$pip_version = (pip -V)

if ($pip_version -notlike "*PIIDigger*") {
    Write-Status "Activate the Virtual Environment first"
    exit
}

$gc_result = (get-command pyinstaller.exe -ErrorAction SilentlyContinue)

$pyi_cmd=$gc_result.Path
if ($pyi_cmd.Length -eq 0) {
    Write-Status "Couldn't find Pyinstaller on the path.  Make sure it's installed in the Virtual Environment"
    exit
}    
Write-Status "Using Pyinstaller from $pyi_cmd"

Write-Status "Cleaning up old binaries in $bin_dir"
Remove-Item $bin_dir -Recurse -Exclude *.md

Push-Location .\src\piidigger

Write-Status "Building piidigger.exe"
Start-Process -FilePath $pyi_cmd -ArgumentList $piidigger_options -NoNewWindow -Wait

Write-Status "Removing build artifacts"
Remove-Item build\, *.spec -Recurse

Pop-Location

if ($NoCodeSign) {
    Write-Status "Skipping Code Signing Step"
} else {
    Write-Status "Signing PIIDigger.exe"
    Push-Location $build_dir
    if (Test-Path $exe_path -PathType Leaf) {
        sign-file -file "piidigger.exe"
    } else {
        Write-Status "PIIDigger.exe does not appear to have been built properly."
        exit
    }
}

Write-Status "Creating PIIDIgger.zip"
$archive=@{
    Path = $bin_dir+"\piidigger\*"
    CompressionLevel = "Fastest"
    DestinationPath = $zip_file
}
Compress-Archive @archive

Write-Status "Writing hash values"
foreach ($hash in $hashes) {
    $hash_path = $bin_dir + "\$hash"
    (Get-FileHash -Path $zip_file -Algorithm $hash).Hash | Out-File -FilePath $hash_path -Encoding ASCII -NoNewline
    Write-Status "Hash $hash written to $hash_path"
}

Write-Status "Cleaning up Build directory"
Pop-Location
Remove-Item $build_dir -Recurse

Write-Status "All done.  Check the $bin_dir directory for updated binaries"

# SIG # Begin signature block
# MIIfYwYJKoZIhvcNAQcCoIIfVDCCH1ACAQExDzANBglghkgBZQMEAgEFADB5Bgor
# BgEEAYI3AgEEoGswaTA0BgorBgEEAYI3AgEeMCYCAwEAAAQQH8w7YFlLCE63JNLG
# KX7zUQIBAAIBAAIBAAIBAAIBADAxMA0GCWCGSAFlAwQCAQUABCAZy5XwGjccCc5M
# 23jqMKsXZ782+2HXdA9xLA8dmb8LTqCCDOgwggZuMIIEVqADAgECAhAtYLGndXgb
# zFvzMEdBS+SKMA0GCSqGSIb3DQEBCwUAMHgxCzAJBgNVBAYTAlVTMQ4wDAYDVQQI
# DAVUZXhhczEQMA4GA1UEBwwHSG91c3RvbjERMA8GA1UECgwIU1NMIENvcnAxNDAy
# BgNVBAMMK1NTTC5jb20gQ29kZSBTaWduaW5nIEludGVybWVkaWF0ZSBDQSBSU0Eg
# UjEwHhcNMjMxMjI3MjAyMDIzWhcNMjUxMjI2MjAyMDIzWjB3MQswCQYDVQQGEwJV
# UzESMBAGA1UECAwJVGVubmVzc2VlMRIwEAYDVQQHDAlOYXNodmlsbGUxHzAdBgNV
# BAoMFktpcmtwYXRyaWNrIFByaWNlIEluYy4xHzAdBgNVBAMMFktpcmtwYXRyaWNr
# IFByaWNlIEluYy4wggGiMA0GCSqGSIb3DQEBAQUAA4IBjwAwggGKAoIBgQCH4MZY
# NZpjmSL0jBcXwN2a/Sj6Q4M0oua16QYbdW1zBK9Cw4mUKEMmo36EAaJZOyvSAdUU
# aj2A5g50fbweYROqbeFC9L8plpS4+bLeGPTOEq1fl50VxHPCmrFOASh1mLhvIGcx
# ZmKKr+p4sgJqpfvZKSPYkGw3EoAoJ6w2HZb7kajrdKqoaZO2IbXYVWjQHwh2EjFX
# 3Pwt2jNQbmQKwQVYglE5REY1dk05PbtvuYD8z/JHImQUbh7UY/9vCbFUoE+Ck1J4
# MUlO+CJNmv/XMXYOo2oCN9HY9hUc8T/1XsH2Kax7ai+nddAqPH7m7nAEtuEqQqC4
# /FSoG4FI10bvbCAQUOAQRx0u+8xjCgJ9+hq3ZJCkWGw+Wt0av40b/fpJGtGllPDd
# dBz/Y6UJNCbUJk8Tk0/h16Tsx/CDSHgvbq965Z54sEL8j798QDgDIv07/+amSwhv
# IAvWbJdsDpMSdWvxtGigxkqMZ4xh1UONOCsKzRklhnFiidJ1qusAg33mifMCAwEA
# AaOCAXMwggFvMAwGA1UdEwEB/wQCMAAwHwYDVR0jBBgwFoAUVML+EJUAk81q9efA
# 19myS7iPDOMwWAYIKwYBBQUHAQEETDBKMEgGCCsGAQUFBzAChjxodHRwOi8vY2Vy
# dC5zc2wuY29tL1NTTGNvbS1TdWJDQS1Db2RlU2lnbmluZy1SU0EtNDA5Ni1SMS5j
# ZXIwUQYDVR0gBEowSDAIBgZngQwBBAEwPAYMKwYBBAGCqTABAwMBMCwwKgYIKwYB
# BQUHAgEWHmh0dHBzOi8vd3d3LnNzbC5jb20vcmVwb3NpdG9yeTATBgNVHSUEDDAK
# BggrBgEFBQcDAzBNBgNVHR8ERjBEMEKgQKA+hjxodHRwOi8vY3Jscy5zc2wuY29t
# L1NTTGNvbS1TdWJDQS1Db2RlU2lnbmluZy1SU0EtNDA5Ni1SMS5jcmwwHQYDVR0O
# BBYEFHoHVzBt4Ei4J6BKiF0XdfJ5O5Q7MA4GA1UdDwEB/wQEAwIHgDANBgkqhkiG
# 9w0BAQsFAAOCAgEAeKQvwVT8ZkDeUVcDXW8sNAtXxBwGPDOh8x1rqNVj73uTp3g1
# wmbOMYYZH4cnWV5/E11fwfkoNpI+fGy1YREWnzsTv+Uw5pymp9ELVrE9tzhJxgog
# u5yM6trSMrzyCql4dWjdjElMRR/eZ0mbzhBXUIk6QcKNOm2xrUh5IOI4IJsC6rwR
# aaAtYWQ+7f3b3iBGkzqFxmnQGsyOfrxH5Etj4awSzSFpc0jYW9SEnrN+c09YfbnO
# Vb5bz6e23RgKBAadNbtBApWRKAxYDnwvpJzfGJxBM+oi9QZc2/loySvdi5LEcCbP
# KFrbgakdm/ZmbS2V8NWUulnYzpSzNx8x9tw6KeGCMP/ti1dcNWULW5ItLOjjaa4T
# VtOze4uu3Y6cqlS3/d11SLL91DJK0kqxAsejP2egwKFjaB38ShCJ/BZUwgYhlycr
# qzgSZX9qfzzkw1XHKZer2Bfbgbwd6zkq0balgk2sAxIE9Hcc6SAWqPo9qhijjJ39
# ZUUOJlracqAgetwg6DzBe7NMqifkXuXmVizgIFUwbYDMSs95PBsWVVGLFUqvLtvA
# jARn7tElqmMPE24fRklS82YxO45nyalAYmrj93+7oMcXlpLVwhoFjsHRBQDcj5CG
# Klb6IybmI8EmTPc87AetRYbmZ+v+a6vvhhECoCkdGl71Dt8M/2vJavh/9M0wggZy
# MIIEWqADAgECAghkM1HTxzifCDANBgkqhkiG9w0BAQsFADB8MQswCQYDVQQGEwJV
# UzEOMAwGA1UECAwFVGV4YXMxEDAOBgNVBAcMB0hvdXN0b24xGDAWBgNVBAoMD1NT
# TCBDb3Jwb3JhdGlvbjExMC8GA1UEAwwoU1NMLmNvbSBSb290IENlcnRpZmljYXRp
# b24gQXV0aG9yaXR5IFJTQTAeFw0xNjA2MjQyMDQ0MzBaFw0zMTA2MjQyMDQ0MzBa
# MHgxCzAJBgNVBAYTAlVTMQ4wDAYDVQQIDAVUZXhhczEQMA4GA1UEBwwHSG91c3Rv
# bjERMA8GA1UECgwIU1NMIENvcnAxNDAyBgNVBAMMK1NTTC5jb20gQ29kZSBTaWdu
# aW5nIEludGVybWVkaWF0ZSBDQSBSU0EgUjEwggIiMA0GCSqGSIb3DQEBAQUAA4IC
# DwAwggIKAoICAQCfgxNzqrDGbSHL24t6h3TQcdyOl3Ka5LuINLTdgAPGL0WkdJq/
# Hg9Q6p5tePOf+lEmqT2d0bKUVz77OYkbkStW72fL5gvjDjmMxjX0jD3dJekBrBdC
# fVgWQNz51ShEHZVkMGE6ZPKX13NMfXsjAm3zdetVPW+qLcSvvnSsXf5qtvzqXHnp
# D0OctVIFD+8+sbGP0EmtpuNCGVQ/8y8Ooct8/hP5IznaJRy4PgBKOm8yMDdkHseu
# dQfYVdIYyQ6KvKNc8HwKp4WBwg6vj5lc02AlvINaaRwlE81y9eucgJvcLGfE3ckJ
# mNVz68Qho+Uyjj4vUpjGYDdkjLJvSlRyGMwnh/rNdaJjIUy1PWT9K6abVa8mTGC0
# uVz+q0O9rdATZlAfC9KJpv/XgAbxwxECMzNhF/dWH44vO2jnFfF3VkopngPawism
# YTJboFblSSmNNqf1x1KiVgMgLzh4gL32Bq5BNMuURb2bx4kYHwu6/6muakCZE93v
# UN8BuvIE1tAx3zQ4XldbyDgeVtSsSKbt//m4wTvtwiS+RGCnd83VPZhZtEPqqmB9
# zcLlL/Hr9dQg1Zc0bl0EawUR0tOSjAknRO1PNTFGfnQZBWLsiePqI3CY5NEv1IoT
# GEaTZeVYc9NMPSd6Ij/D+KNVt/nmh4LsRR7Fbjp8sU65q2j3m2PVkUG8qQIDAQAB
# o4H7MIH4MA8GA1UdEwEB/wQFMAMBAf8wHwYDVR0jBBgwFoAU3QQJB6L1en1SUxKS
# le44gCUNplkwMAYIKwYBBQUHAQEEJDAiMCAGCCsGAQUFBzABhhRodHRwOi8vb2Nz
# cHMuc3NsLmNvbTARBgNVHSAECjAIMAYGBFUdIAAwEwYDVR0lBAwwCgYIKwYBBQUH
# AwMwOwYDVR0fBDQwMjAwoC6gLIYqaHR0cDovL2NybHMuc3NsLmNvbS9zc2wuY29t
# LXJzYS1Sb290Q0EuY3JsMB0GA1UdDgQWBBRUwv4QlQCTzWr158DX2bJLuI8M4zAO
# BgNVHQ8BAf8EBAMCAYYwDQYJKoZIhvcNAQELBQADggIBAPUPJodwr5miyvXWyfCN
# Zj05gtOII9iCv49UhCe204MH154niU2EjlTRIO5gQ9tXQjzHsJX2vszqoz2OTwbG
# K1mGf+tzG8rlQCbgPW/M9r1xxs19DiBAOdYF0q+UCL9/wlG3K7V7gyHwY9rlnOFp
# LnUdTsthHvWlM98CnRXZ7WmTV7pGRS6AvGW+5xI+3kf/kJwQrfZWsqTU+tb8LryX
# IbN2g9KR+gZQ0bGAKID+260PZ+34fdzZcFt6umi1s0pmF4/n8OdX3Wn+vF7h1Yyf
# E7uVmhX7eSuF1W0+Z0duGwdc+1RFDxYRLhHDsLy1bhwzV5Qe/kI0Ro4xUE7bM1eV
# +jjk5hLbq1guRbfZIsr0WkdJLCjoT4xCPGRo6eZDrBmRqccTgl/8cQo3t51Qezxd
# 96JSgjXktefTCm9r/o35pNfVHUvnfWII+NnXrJlJ27WEQRQu9i5gl1NLmv7xiHp0
# up516eDap8nMLDt7TAp4z5T3NmC2gzyKVMtODWgqlBF1JhTqIDfM63kXdlV4cW3i
# STgzN9vkbFnHI2LmvM4uVEv9XgMqyN0eS3FE0HU+MWJliymm7STheh2ENH+kF3y0
# rH0/NVjLw78a3Z9UVm1F5VPziIorMaPKPlDRADTsJwjDZ8Zc6Gi/zy4WZbg8Zv87
# spWrmo2dzJTw7XhQf+xkR6OdMYIR0TCCEc0CAQEwgYwweDELMAkGA1UEBhMCVVMx
# DjAMBgNVBAgMBVRleGFzMRAwDgYDVQQHDAdIb3VzdG9uMREwDwYDVQQKDAhTU0wg
# Q29ycDE0MDIGA1UEAwwrU1NMLmNvbSBDb2RlIFNpZ25pbmcgSW50ZXJtZWRpYXRl
# IENBIFJTQSBSMQIQLWCxp3V4G8xb8zBHQUvkijANBglghkgBZQMEAgEFAKB8MBAG
# CisGAQQBgjcCAQwxAjAAMBkGCSqGSIb3DQEJAzEMBgorBgEEAYI3AgEEMBwGCisG
# AQQBgjcCAQsxDjAMBgorBgEEAYI3AgEVMC8GCSqGSIb3DQEJBDEiBCD1MSR1yzLZ
# pafAAQbWqGLCuaicN9UscxXy70bD+tefazANBgkqhkiG9w0BAQEFAASCAYA3rq5m
# TpLgQSh/gj1lboYeIRbdv91YsfMT971v6Xv6SZujaBiXcPpT2xwiGFDZWLZ52DeJ
# hWQWqob7ehpAFE2pBxsS7k0CBRv+OrT1tBR/m9peqSKWRQVzk9ZByB0+CJGI1jn4
# K071zPR9dHBI51VOm/Rs0ebiAMdtF29fm3Kla4ymfF6bR39FFZK3V+KGnmuYqeTN
# ppVj5AQp43DWmPuuQ1ORLCm1Q8bq/ASvPTlJ4GdGxreMo8l9V/wx4VWUGPhkgbXA
# vXpDw1w1cwwYwQxGpithAScyNlKp5Mpi7yjvepV5IkCRFiHa0pngoVErDHYIN5X5
# Re90CotP+KWS2VK18JWKK3QR6sL2fkDuy48G3RvwOcahhUDeiTvexINWOm6d5ZJn
# iiH6hzp5JADVyL7e7/PUMT5VA5v41rYr923cw16n+p+P4m3P03We2dzLVL6K37H5
# +egZNCpZAh1cU751BS5yrRbW4dPFAFULAZkBWzEfn3XZicDSpw76P+4eIwChgg8X
# MIIPEwYKKwYBBAGCNwMDATGCDwMwgg7/BgkqhkiG9w0BBwKggg7wMIIO7AIBAzEN
# MAsGCWCGSAFlAwQCATB3BgsqhkiG9w0BCRABBKBoBGYwZAIBAQYMKwYBBAGCqTAB
# AwYBMDEwDQYJYIZIAWUDBAIBBQAEIA0qixuicSB/lIRX1KaWVDYwilEXFs5csjsM
# A4nTPphTAggSWZQOiQynRxgPMjAyNDA0MzAxODQwMjNaMAMCAQGgggwAMIIE/DCC
# AuSgAwIBAgIQWlqs6Bo1brRiho1XfeA9xzANBgkqhkiG9w0BAQsFADBzMQswCQYD
# VQQGEwJVUzEOMAwGA1UECAwFVGV4YXMxEDAOBgNVBAcMB0hvdXN0b24xETAPBgNV
# BAoMCFNTTCBDb3JwMS8wLQYDVQQDDCZTU0wuY29tIFRpbWVzdGFtcGluZyBJc3N1
# aW5nIFJTQSBDQSBSMTAeFw0yNDAyMTkxNjE4MTlaFw0zNDAyMTYxNjE4MThaMG4x
# CzAJBgNVBAYTAlVTMQ4wDAYDVQQIDAVUZXhhczEQMA4GA1UEBwwHSG91c3RvbjER
# MA8GA1UECgwIU1NMIENvcnAxKjAoBgNVBAMMIVNTTC5jb20gVGltZXN0YW1waW5n
# IFVuaXQgMjAyNCBFMTBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABKdhcvUw6XrE
# gxSWBULj3Oid25Rt2TJvSmLLaLy3cmVATADvhyMryD2ZELwYfVwABUwivwzYd1ml
# WCRXUtcEsHyjggFaMIIBVjAfBgNVHSMEGDAWgBQMnRAljpqnG5mHQ88IfuG9gZD0
# zzBRBggrBgEFBQcBAQRFMEMwQQYIKwYBBQUHMAKGNWh0dHA6Ly9jZXJ0LnNzbC5j
# b20vU1NMLmNvbS10aW1lU3RhbXBpbmctSS1SU0EtUjEuY2VyMFEGA1UdIARKMEgw
# PAYMKwYBBAGCqTABAwYBMCwwKgYIKwYBBQUHAgEWHmh0dHBzOi8vd3d3LnNzbC5j
# b20vcmVwb3NpdG9yeTAIBgZngQwBBAIwFgYDVR0lAQH/BAwwCgYIKwYBBQUHAwgw
# RgYDVR0fBD8wPTA7oDmgN4Y1aHR0cDovL2NybHMuc3NsLmNvbS9TU0wuY29tLXRp
# bWVTdGFtcGluZy1JLVJTQS1SMS5jcmwwHQYDVR0OBBYEFFBPJKzvtT5jEyMJkibs
# ujqW5F0iMA4GA1UdDwEB/wQEAwIHgDANBgkqhkiG9w0BAQsFAAOCAgEAmKCPAwCR
# vKvEZEF/QiHiv6tsIHnuVO7BWILqcfZ9lJyIyiCmpLOtJ5VnZ4hvm+GP2tPuOpZd
# mfTYWdyzhhOsDVDLElbfrKMLiOXn9uwUJpa5fMZe3Zjoh+n/8DdnSw1MxZNMGhuZ
# x4zeyqei91f1OhEU/7b2vnJCc9yBFMjY++tVKovFj0TKT3/Ry+Izdbb1gGXTzQQ1
# uVFy7djxGx/NG1VP/aye4OhxHG9FiZ3RM9oyAiPbEgjrnVCc+nWGKr3FTQDKi8vN
# uyLnCVHkiniL+Lz7H4fBgk163Llxi11Ynu5A/phpm1b+M2genvqo1+2r8iVLHrER
# gFGMUHEdKrZ/OFRDmgFrCTY6xnaPTA5/ursCqMK3q3/59uZaOsBZhZkaP9EuOW2p
# 0U8Gkgqp2GNUjFoaDNWFoT/EcoGDiTgN8VmQFgn0Fa4/3dOb6lpYEPBcjsWDdqUa
# xugStY9aW/AwCal4lSN4otljbok8u31lZx5NVa4jK6N6upvkgyZ6osmbmIWr9DLh
# g8bI+KiXDnDWT0547gSuZLYUq+TV6O/DhJZH5LVXJaeS1jjjZZqhK3EEIJVZl0xY
# V4H4Skvy6hA2rUyFK3+whSNS52TJkshsxVCOPtvqA9ecPqZLwWBaIICG4zVr+GAD
# 7qjWwlaLMd2ZylgOHI3Oit/0pVETqJHutyYwggb8MIIE5KADAgECAhBtUhhwh+gj
# TYVgANCAj5NWMA0GCSqGSIb3DQEBCwUAMHwxCzAJBgNVBAYTAlVTMQ4wDAYDVQQI
# DAVUZXhhczEQMA4GA1UEBwwHSG91c3RvbjEYMBYGA1UECgwPU1NMIENvcnBvcmF0
# aW9uMTEwLwYDVQQDDChTU0wuY29tIFJvb3QgQ2VydGlmaWNhdGlvbiBBdXRob3Jp
# dHkgUlNBMB4XDTE5MTExMzE4NTAwNVoXDTM0MTExMjE4NTAwNVowczELMAkGA1UE
# BhMCVVMxDjAMBgNVBAgMBVRleGFzMRAwDgYDVQQHDAdIb3VzdG9uMREwDwYDVQQK
# DAhTU0wgQ29ycDEvMC0GA1UEAwwmU1NMLmNvbSBUaW1lc3RhbXBpbmcgSXNzdWlu
# ZyBSU0EgQ0EgUjEwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQCuURAT
# 0vk8IKAghd7JUBxkyeH9xek0/wp/MUjoclrFXqhh/fGH91Fc+7fm0MHCE7A+wmOi
# qBj9ODrJAYGq3rm33jCnHSsCBNWAQYyoauLq8IjqsS1JlXL29qDNMMdwZ8UNzQS7
# vWZMDJ40JSGNphMGTIA2qn2bohGtgRc4p1395ESypUOaGvJ3t0FNL3BuKmb6YctM
# cQUF2sqooMzd89h0E6ujdvBDo6ZwNnWoxj7YmfWjSXg33A5GuY9ym4QZM5OEVgo8
# ebz/B+gyhyCLNNhh4Mb/4xvCTCMVmNYrBviGgdPZYrym8Zb84TQCmSuX0JlLLa6W
# K1aO6qlwISbb9bVGh866ekKblC/XRP20gAu1CjvcYciUgNTrGFg8f8AJgQPOCc1/
# CCdaJSYwhJpSdheKOnQgESgNmYZPhFOC6IKaMAUXk5U1tjTcFCgFvvArXtK4azAW
# UOO1Y3fdldIBL6LjkzLUCYJNkFXqhsBVcPMuB0nUDWvLJfPimstjJ8lF4S6ECxWn
# lWi7OElVwTnt1GtRqeY9ydvvGLntU+FecK7DbqHDUd366UreMkSBtzevAc9aqoZP
# njVMjvFqV1pYOjzmTiVHZtAc80bAfFe5LLfJzPI6DntNyqobpwTevQpHqPDN9qqN
# O83r3kaw8A9j+HZiSw2AX5cGdQP0kG0vhzfgBwIDAQABo4IBgTCCAX0wEgYDVR0T
# AQH/BAgwBgEB/wIBADAfBgNVHSMEGDAWgBTdBAkHovV6fVJTEpKV7jiAJQ2mWTCB
# gwYIKwYBBQUHAQEEdzB1MFEGCCsGAQUFBzAChkVodHRwOi8vd3d3LnNzbC5jb20v
# cmVwb3NpdG9yeS9TU0xjb21Sb290Q2VydGlmaWNhdGlvbkF1dGhvcml0eVJTQS5j
# cnQwIAYIKwYBBQUHMAGGFGh0dHA6Ly9vY3Nwcy5zc2wuY29tMD8GA1UdIAQ4MDYw
# NAYEVR0gADAsMCoGCCsGAQUFBwIBFh5odHRwczovL3d3dy5zc2wuY29tL3JlcG9z
# aXRvcnkwEwYDVR0lBAwwCgYIKwYBBQUHAwgwOwYDVR0fBDQwMjAwoC6gLIYqaHR0
# cDovL2NybHMuc3NsLmNvbS9zc2wuY29tLXJzYS1Sb290Q0EuY3JsMB0GA1UdDgQW
# BBQMnRAljpqnG5mHQ88IfuG9gZD0zzAOBgNVHQ8BAf8EBAMCAYYwDQYJKoZIhvcN
# AQELBQADggIBAJIZdQ2mWkLPGQfZ8vyU+sCb8BXpRJZaL3Ez3VDlE3uZk3cPxPty
# bVfLuqaci0W6SB22JTMttCiQMnIVOsXWnIuAbD/aFTcUkTLBI3xys+wEajzXaXJY
# WACDS47BRjDtYlDW14gLJxf8W6DQoH3jHDGGy8kGJFOlDKG7/YrK7UGfHtBAEDVe
# 6lyZ+FtCsrk7dD/IiL/+Q3Q6SFASJLQ2XI89ihFugdYL77CiDNXrI2MFspQGswXE
# AGpHuaQDTHUp/LdR3TyrIsLlnzoLskUGswF/KF8+kpWUiKJNC4rPWtNrxlbXYRGg
# dEdx8SMjUTDClldcrknlFxbqHsVmr9xkT2QtFmG+dEq1v5fsIK0vHaHrWjMMmaJ9
# i+4qGJSD0stYfQ6v0PddT7EpGxGd867Ada6FZyHwbuQSadMb0K0P0OC2r7rwqBUe
# 0BaMqTa6LWzWItgBjGcObXeMxmbQqlEz2YtAcErkZvh0WABDDE4U8GyV/32FdaAv
# JgTfe9MiL2nSBioYe/g5mHUSWAay/Ip1RQmQCvmF9sNfqlhJwkjy/1U1ibUkTIUB
# X3HgymyQvqQTZLLys6pL2tCdWcjI9YuLw30rgZm8+K387L7ycUvqrmQ3ZJlujHl3
# r1hgV76s3WwMPgKk1bAEFMj+rRXimSC+Ev30hXZdqyMdl/il5Ksd0vhGMYICWTCC
# AlUCAQEwgYcwczELMAkGA1UEBhMCVVMxDjAMBgNVBAgMBVRleGFzMRAwDgYDVQQH
# DAdIb3VzdG9uMREwDwYDVQQKDAhTU0wgQ29ycDEvMC0GA1UEAwwmU1NMLmNvbSBU
# aW1lc3RhbXBpbmcgSXNzdWluZyBSU0EgQ0EgUjECEFparOgaNW60YoaNV33gPccw
# CwYJYIZIAWUDBAIBoIIBYTAaBgkqhkiG9w0BCQMxDQYLKoZIhvcNAQkQAQQwHAYJ
# KoZIhvcNAQkFMQ8XDTI0MDQzMDE4NDAyM1owKAYJKoZIhvcNAQk0MRswGTALBglg
# hkgBZQMEAgGhCgYIKoZIzj0EAwIwLwYJKoZIhvcNAQkEMSIEIBTbaenLn0ZPLANY
# 3zFs2AyydaYcacirmc0rKNNiCf9qMIHJBgsqhkiG9w0BCRACLzGBuTCBtjCBszCB
# sAQgnXF/jcI3ZarOXkqw4fV115oX1Bzu2P2v7wP9Pb2JR+cwgYswd6R1MHMxCzAJ
# BgNVBAYTAlVTMQ4wDAYDVQQIDAVUZXhhczEQMA4GA1UEBwwHSG91c3RvbjERMA8G
# A1UECgwIU1NMIENvcnAxLzAtBgNVBAMMJlNTTC5jb20gVGltZXN0YW1waW5nIElz
# c3VpbmcgUlNBIENBIFIxAhBaWqzoGjVutGKGjVd94D3HMAoGCCqGSM49BAMCBEgw
# RgIhAPFsH3D9FZqT1luHZoijzlWsevQkaF2V94BRHW5eeM14AiEA/vORMlpa9kkK
# buuN9vqwVgBjN2A6hRUYBa271VVYMIY=
# SIG # End signature block
