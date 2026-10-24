@echo off
REM Compile and run the CloudSim simulation (Windows)
REM Needs: JDK 8+ and CloudSim jar in cloudsim\lib\ (see README)
if not exist lib\cloudsim-3.0.3.jar (
  echo Put cloudsim-3.0.3.jar inside the cloudsim\lib folder first. See README.md
  exit /b 1
)
if not exist out mkdir out
javac -cp "lib\*" -d out src\LostFoundSimulation.java
if errorlevel 1 exit /b 1
java -cp "out;lib\*" LostFoundSimulation
