---
title: VPN Setup Guide
allowed_groups: [all-employees]
sensitivity: General
owner: IT Network Team
---
# VPN Setup Guide

Contoso uses the GlobalProtect VPN client to give employees secure remote access to internal applications. You need VPN for the intranet, file shares and internal finance tools when you are not in the office. Microsoft 365 apps such as Outlook and Teams do not require VPN.

## Installing the VPN client on Windows

1. Open Company Portal from the Start menu.
2. Search for "GlobalProtect VPN" and select Install.
3. When installation finishes, open GlobalProtect from the system tray.
4. Enter the portal address vpn.contoso.example and select Connect.
5. Sign in with your Contoso work account and approve the Microsoft Authenticator prompt.

## Installing the VPN client on macOS

1. Open Self Service from the Applications folder.
2. Install "GlobalProtect VPN".
3. Allow the system extension when macOS asks: open System Settings, Privacy and Security, and select Allow.
4. Enter the portal address vpn.contoso.example and sign in with your work account.

## Troubleshooting VPN connection problems

If the VPN does not connect, check that your internet connection works without VPN first. Restart the GlobalProtect client and try again. If you see the error "Gateway not responding", switch to a different network such as a mobile hotspot, because some hotel and public networks block VPN traffic. If your account is locked you will not be able to sign in to VPN; reset your password first. If the problem continues for more than 30 minutes, create an IT ticket with the error message shown in the client.
