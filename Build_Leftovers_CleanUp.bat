@echo off

del *.spec
if exist build rmdir build /S /Q
if exist dist rmdir dist /S /Q
if exist .vs rmdir .vs /S /Q
if exist VSBuildStopwatch.Extension\bin rmdir VSBuildStopwatch.Extension\bin /S /Q
if exist VSBuildStopwatch.Extension\obj rmdir VSBuildStopwatch.Extension\obj /S /Q
