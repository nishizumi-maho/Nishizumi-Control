# Dominant Control for iRacing

<img width="1024" height="1024" alt="ChatGPT Image Dec 11, 2025, 12_52_14 PM" src="https://github.com/user-attachments/assets/0fd4acb4-c07f-4919-9219-4d69d3b3a0a7" />



An accessibility and educational tool for iRacing that helps drivers manage vehicle controls, designed for those with physical limitations or equipment constraints.

![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-blue.svg)
![Status](https://img.shields.io/badge/status-stable-green.svg)

## ⚠️ Important Notice

**This software is designed to assist:**
- Drivers with physical disabilities or mobility limitations
- Users whose equipment lacks certain control features
- Students and educators learning about telemetry systems
- Hobbyists experimenting in practice sessions

**Intended Use:**
- Accessibility assistance for those with genuine physical needs
- Educational purposes for learning and experimentation
- Private practice and testing environments

Users should ensure their use aligns with their specific needs and circumstances.

## Overview

This application provides an accessible interface for managing vehicle settings in iRacing, particularly helpful for:
- **Drivers with mobility limitations** who cannot easily reach all physical controls
- **Equipment without built-in controls** (wheels lacking rotary knobs, button boxes, etc.)
- **Learning and education** about telemetry systems and vehicle dynamics
- **Private testing sessions** to understand control adjustments

## Key Features

### Accessibility Support
- **Simplified Control Access**: Map vehicle adjustments to accessible input devices
- **Customizable Interface**: Adapt controls to individual physical needs and limitations
- **Visual Feedback**: Real-time display of current settings via HUD overlay
- **Flexible Input**: Support for keyboard, standard game controllers, or specialized accessibility devices

### Educational Components
- **Telemetry Visualization**: Learn how vehicle parameters behave in real-time
- **Control Mapping**: Understand relationships between inputs and vehicle behavior
- **Data Analysis**: Study how adjustments affect vehicle dynamics
- **Configuration Management**: Experiment with different setup approaches

### Technical Features
- Real-time telemetry monitoring
- Customizable heads-up display (HUD)
- Per-car configuration storage
- Multi-input device support
- Profile management system

## Download & Installation

### System Requirements
- **OS**: Windows 10/11 (64-bit)
- **RAM**: 4 GB minimum (8 GB recommended)
- **Disk Space**: 100 MB
- **iRacing**: Valid subscription and installation

### Installation Steps

1. **Download** the latest release from the Releases section
2. **Extract** the ZIP file to a location of your choice (e.g., `C:\DominantControl\`)
3. **Run** `DominantControl.exe`

**That's it!** No Python installation or dependencies required.

### Windows Security Note
Windows may show a security warning for unrecognized applications:
- Click "More info" → "Run anyway"
- This is normal for applications without expensive code-signing certificates

## Quick Start Guide

**⚠️ IMPORTANT: Startup Order for Force Feedback**

To avoid force feedback issues:
1. **Start this application FIRST**
2. **Then start iRacing**
3. **After sim loads, scan controls**

Or simply enable **"Keyboard Only Mode"** in settings to eliminate this concern entirely.

### Setup Steps

1. **Launch iRacing** and enter a **private practice session**
2. **Run the application** (`iRacingControlManager.exe`)
3. **Enter CONFIG mode** by clicking the mode button
4. **Click "Scan Driver Controls"** to detect available vehicle adjustments
5. **Configure keys** for each control you need to access
6. **Set up presets** for quick access to specific values
7. **Switch to RUNNING mode** when ready
8. **Test thoroughly in private sessions** before any other use

**Important**: Always test in private, non-competitive sessions first.

## Basic Usage

### Configuration Mode
Use CONFIG mode to:
- Set up which controls you need access to
- Map keyboard keys or controller buttons to adjustments
- Create preset values for quick access
- Customize the HUD overlay
- Save configurations per car/track

### Running Mode
In RUNNING mode:
- The application monitors your vehicle telemetry
- Press your configured buttons to adjust controls
- HUD shows real-time values
- All adjustments happen through your configured inputs

## For Accessibility Users

If you have physical limitations that make reaching certain controls difficult:

1. **Identify which controls you cannot easily reach**
   - Brake bias adjustments
   - Traction control levels
   - Anti-roll bar adjustments
   - Other driver-adjustable settings

2. **Map them to accessible inputs**
   - Keyboard keys you can reach
   - Controller buttons in easy positions
   - Specialized accessibility devices

3. **Create presets for common values**
   - Quick access without multiple presses
   - Reduce physical strain

4. **Test in private sessions**
   - Ensure everything works for your needs
   - Adjust timings if needed

## For Educational Use

Students and learners can use this to:
- **Study telemetry**: See how vehicle parameters change in real-time
- **Understand relationships**: Learn how settings affect behavior
- **Experiment safely**: Test different configurations in private sessions
- **Analyze data**: Observe patterns in vehicle dynamics
- **Learn APIs**: Understand how racing sim telemetry systems work

## Configuration Management

The application stores all settings in:
```
%APPDATA%\DominantControl\configs\
```

This includes:
- Per-car control configurations
- Per-track presets
- HUD display preferences
- Input device mappings
- Timing profiles

Your configurations persist across updates and sessions.

## HUD Overlay

The customizable heads-up display shows:
- Current values of monitored controls
- Real-time updates
- Customizable colors, fonts, and opacity
- Draggable positioning
- Per-car variable selection

Configure what to display in the "HUD / Overlay" tab.

## Device Support

### Supported Input Devices
- Keyboard (always available)
- Game controllers / Joysticks
- Wheel button boxes
- Generic USB input devices

### Device Management
- Enable/disable specific devices
- Keyboard-only mode available
- Selective device permissions
- Accessible through the "Manage Devices" button

## Troubleshooting

### Force Feedback Issue

**Problem**: Force feedback stops working

**Cause**: Technical limitation of Python's joystick handling library when initialized after iRacing

**Solutions**:
1. **Prevention**: Always start the app BEFORE starting iRacing
2. **Quick fix**: Restart iRacing (app can stay open)
3. **Permanent solution**: Enable "Keyboard Only Mode" in settings (recommended if not using controller buttons)

See INSTALLATION.md and FAQ.md for detailed information.

### Application Won't Start
- Ensure you have Windows 10/11 (64-bit)
- Run as Administrator if needed
- Check Windows Defender didn't quarantine it
- Verify extraction completed fully

### Cannot Connect to iRacing
- Ensure iRacing is running
- Must be in a session (not menus)
- Try restarting both applications
- Check you're in Drive mode in the car

### Controls Not Responding
- Verify you're in RUNNING mode (not CONFIG)
- Check keys are properly configured
- Ensure correct input device is enabled
- Test in a private practice session first

### HUD Not Visible
- Check "Options" → "Show/Hide Overlay"
- Verify variables are marked visible in HUD tab
- Adjust opacity if it's too transparent
- Try dragging it to a different screen position

## Legal & Compliance

### Terms of Service Compliance
Users must ensure their use complies with all applicable terms of service. This software:
- Provides an interface for control access
- Is intended as an accessibility aid
- Should only be used in accordance with platform rules
- Requires user responsibility for compliant usage

### Disclaimer
This software is provided for educational and accessibility purposes only. Users are solely responsible for:
- Understanding and following all applicable rules and terms of service
- Using the software ethically and responsibly
- Ensuring their usage is appropriate for their situation
- Any consequences of use

**See DISCLAIMER.md for complete legal information.**

### Not Affiliated
This project is not affiliated with, endorsed by, or sponsored by iRacing.com Motorsport Simulations, LLC.

## Privacy & Security

This application:
- ✅ Stores all configuration locally only
- ✅ Does not transmit any data externally
- ✅ Does not collect user information
- ✅ Operates entirely offline (except iRacing telemetry API)
- ✅ No telemetry, tracking, or analytics
- ✅ No internet connection required (except for iRacing itself)

Your data never leaves your computer.

## Distribution & Privacy

**Closed-Source Software**: This is proprietary software distributed to authorized users.

**Respectful Distribution**:
- Please don't share publicly or redistribute
- Helps maintain tool integrity and availability
- Protects the community of legitimate users
- See LICENSE.md for complete terms

**Privacy Commitment**:
- All data stored locally only
- No external connections (except iRacing telemetry API)
- No tracking, analytics, or data collection
- Complete offline operation

## Responsible Use

This tool was created to help drivers with genuine accessibility needs and for educational purposes. 

**We encourage:**
- Using for legitimate accessibility assistance
- Learning about telemetry and sim racing systems
- Experimenting and testing in practice sessions
- Being honest about your use case and needs

**Please respect:**
- The spirit of fair competition
- Other drivers and the community
- Platform terms of service
- The tool's intended purpose

Users are responsible for ensuring their usage is appropriate for their individual circumstances.

## Support & Documentation

### Getting Help
- Review all documentation thoroughly (multiple guides included)
- Check FAQ.md for common questions and solutions
- See INSTALLATION.md for setup and troubleshooting
- Contact through appropriate private channels if needed

### Documentation Included
- **QUICKSTART.md** - Get running in 5 minutes
- **INSTALLATION.md** - Detailed setup instructions
- **FAQ.md** - Common questions answered
- **DISCLAIMER.md** - Legal information
- **LICENSE.md** - Terms of use
- **CHANGELOG.md** - Version history
- **TECHNICAL.md** - Technical specifications

## Updates

Check the Releases section periodically for updates:
- Download the new version
- Extract to the same location (overwrite files)
- Your configurations are automatically preserved
- Read changelog for new features or changes

## Distribution

**This software is proprietary and closed-source:**
- ❌ Do not redistribute or share
- ❌ Do not reverse engineer
- ❌ Do not modify or create derivatives
- ❌ Keep private and confidential
- ✅ Use only as authorized

See LICENSE for complete terms.

## Final Reminder

This tool is designed to help drivers with genuine accessibility needs and for educational purposes. Please use it responsibly, ethically, and in accordance with all applicable rules and guidelines.

**Your responsibility. Your choice. Use wisely.**

---

**Version**: 1.0.0  
**Platform**: Windows 10/11 (64-bit)  
**Status**: Stable release  
**Distribution**: Authorized users only
