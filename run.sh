#!/bin/bash

# Adjust permissions for the control.authcookie file
sudo chmod 644 /run/tor/control.authcookie

# Start the Flask application
python3 app.py
