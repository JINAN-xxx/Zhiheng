#Requires -RunAsAdministrator
[CmdletBinding()]
param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$platformPort = 12580
$platformSubnets = @('172.29.106.0/24', '172.29.107.0/24')
$windowsRuleName = 'Zhiheng-Platform-LAN-12580'
$hyperVRuleName = 'Zhiheng-Platform-WSL-LAN-12580'
$wslCreatorId = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'

if ($Remove) {
    Get-NetFirewallRule -Name $windowsRuleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
    Get-NetFirewallHyperVRule -Name $hyperVRuleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallHyperVRule
    Write-Output 'Removed only the two Zhiheng platform LAN rules.'
    exit 0
}

$windowsParameters = @{
    Direction = 'Inbound'
    Action = 'Allow'
    Enabled = 'True'
    Profile = 'Any'
    Protocol = 'TCP'
    LocalPort = $platformPort
    RemoteAddress = $platformSubnets
}
if (Get-NetFirewallRule -Name $windowsRuleName -ErrorAction SilentlyContinue) {
    Set-NetFirewallRule -Name $windowsRuleName @windowsParameters
} else {
    New-NetFirewallRule -Name $windowsRuleName -DisplayName 'Zhiheng platform LAN TCP 12580' @windowsParameters |
        Out-Null
}

$hyperVParameters = @{
    VMCreatorId = $wslCreatorId
    Direction = 'Inbound'
    Action = 'Allow'
    Enabled = 'True'
    Protocol = 'TCP'
    LocalPorts = $platformPort
    RemoteAddresses = $platformSubnets
}
if (Get-NetFirewallHyperVRule -Name $hyperVRuleName -ErrorAction SilentlyContinue) {
    Set-NetFirewallHyperVRule -Name $hyperVRuleName @hyperVParameters
} else {
    New-NetFirewallHyperVRule -Name $hyperVRuleName -DisplayName 'Zhiheng WSL LAN TCP 12580' @hyperVParameters |
        Out-Null
}

$windowsRule = Get-NetFirewallRule -PolicyStore ActiveStore -Name $windowsRuleName
$hyperVRule = Get-NetFirewallHyperVRule -PolicyStore ActiveStore -Name $hyperVRuleName
if ($windowsRule.Enabled -ne 'True' -or $windowsRule.Action -ne 'Allow' -or
    $hyperVRule.Enabled -ne 'True' -or $hyperVRule.Action -ne 'Allow') {
    throw 'Platform rules are not active. Check the effective Windows firewall policy.'
}

Write-Output 'Allowed TCP 12580 from 172.29.106.0/24 and 172.29.107.0/24.'
Write-Output 'Platform URL: http://172.29.106.4:12580/'
