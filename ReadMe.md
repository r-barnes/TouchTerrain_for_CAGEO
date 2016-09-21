Version 0.10 of the TouchTerrain project, primarily a set of python source code files
for Python 2.7:

TouchTerrain-app.py: a server module (service) to be run as part of a Google App Engine server. 
    The server creates a webpage, through which the user inputs the area selection and print parameters.
    
index.html: HTML template for the main webpage, includes Javascript
    
TouchTerrain_standalone.py: A stand-alone version in which the user input is given 
    in a JSON file, rather then via a web page.
    
TouchTerrainEarthEngine.py: With the user input, gets the DEM raster (geotiff) from the 
    Google Earth Engine data server and, using the grid class, creates the 3D models (tiles).
    
grid_tesselate.py: defines the grid class used to create a triangle "mesh" and save it in
    the desired file format (STL or OBJ)
    
Coordinate_system_conv.py, InMemoryZip.py: utility functions  

config.py: used for oauth credentials for the Google dev (Earth Engine) account 

tmp: contains an example terrain model, a zipped stl file 

Note that running the server or the stand-alone version does require some additional setup,
it cannot be run right away with just those files! 
https://developers.google.com/earth-engine/python_install describes the setup of 
the Earth Engine Python API, including the required oauth authentication to the Earth Engine. 
This will create a credentials file on your system and a private key (.pem) file that are 
needed by config.py. You will also need to have install all required third party modules, 
such as numpy and pillow.

