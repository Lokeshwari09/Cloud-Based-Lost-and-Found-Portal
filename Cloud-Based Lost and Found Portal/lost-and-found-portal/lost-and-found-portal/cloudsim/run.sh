#!/usr/bin/env bash
# Compile and run the CloudSim simulation (Linux / macOS)
set -e
if [ ! -f lib/cloudsim-3.0.3.jar ]; then
  echo "Put cloudsim-3.0.3.jar inside the cloudsim/lib folder first. See README.md"
  exit 1
fi
mkdir -p out
javac -cp "lib/*" -d out src/LostFoundSimulation.java
java -cp "out:lib/*" LostFoundSimulation
