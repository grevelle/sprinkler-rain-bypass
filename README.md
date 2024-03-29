# Sprinkler-rain-bypass
This program is allows your Raspberry Pi to replace the rain bypass controller for your sprinkler system. It is written in Python 3.

This program started from http://www.thirdeyevis.com/pi-page-3.php by Scott Mangold. The original program was written for the weather underground and darksky APIs, both of which are no longer available. See Mangold's page for instructions. I made the following changes to his design:
- Changed weather API to Visual Crossing
- Added Google Maps API to get Lat / Lon
- Simplified the hardware to incorporate a pre-assembled relay rather than building one from components
- Reduced the number of LEDs required
- Changed how the configuration file works
- General code optimizations

# Required parts and API access
- 1 Raspberry Pi with active internet connection and GPIO library installed (I used a Raspberry Pi Zero W and added GPIO Pins)
- 1 Raspberry Pi breakout board (many are available, you can also use a breadboard if you want a no solder option)
- 2 50-300 ohm resistors
- 1 single channel 3V relay module
- 1 Green led
- 1 Red led
- Irrigation controller with rain-bypass option
- Visual Crossing developer API account (free)
- Google maps developer API account (free)

# Order of the program
- Open libraries and Config File
- Determine Lat/Lon from Google
- Get precip amount from Visual Crossing
- Set status
- Set GPIO Pins

# Using systemd to automatically run on startup
Place file in directory where you want it to run, I use root for the instructions below

## Make files readable, writable and executable
chmod +777 rain-bypass.py settings.ini

## Create file:
sudo nano /lib/systemd/system/rain-bypass.service

## Configure file:
[Unit]  
Description=Rain bypass script  
After=multi-user.target  
  
[Service]  
ExecStart=/usr/bin/python3 /rain-bypass.py  
Restart=on-failure  
RestartSec=300s  
  
[Install]  
WantedBy=multi-user.target  

## Tell systemd to recognize our service
sudo systemctl daemon-reload

## Tell systemd that we want our service to start on boot
sudo systemctl enable rain-bypass.service

## Reboot and check if it works
systemctl status rain-bypass.service

# Images
Front View
![Picture1](IMG_0123.JPG)

Side View
![Picture1](IMG_0124.JPG)
