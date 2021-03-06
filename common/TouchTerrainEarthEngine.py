"""
TouchTerrainEarthEngine  - creates 3D model tiles from DEM (via Google Earth Engine)
"""

'''
@author:     Chris Harding
@license:    GPL
@contact:    charding@iastate.edu

  This program is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.
  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''

# changes:
# CH Jan.18, 17: ee init is now done before this is imported, so we can have a
#                config.py/.pem file based server version and a google auth based stand alone version
# CH Dec.6, 16: Extended exception catching around getInfo() based on an error I got with ETOPO. 

import sys
import os

import ee

import datetime
from StringIO import StringIO
import urllib2
import socket
import io
import zipfile
import math
import httplib

from grid_tesselate import grid      # my own grid class, creates a mesh from DEM raster
from InMemoryZip import InMemoryZip  # in-memory zip file
from Coordinate_system_conv import * # arc to meters conversion

import numpy
from PIL import Image, ImageDraw, ImageFont

import logging
import time
import random
logging.Formatter.converter = time.gmtime 


# this is my own log file, has nothing to to with the official server logging module!
#USE_LOG = False # prints to stdout instead of a log file  
USE_LOG = True # 
    
#  List of DEM sources  Earth engine offers
DEM_sources = ["""USGS/NED""", """USGS/GMTED2010""", """NOAA/NGDC/ETOPO1""", """USGS/SRTMGL1_003""",
               """AU/GA/AUSTRALIA_5M_DEM"""]

def get_zipped_tiles(DEM_name=None, trlat=None, trlon=None, bllat=None, bllon=None, # all args are keywords, so I can use just **args in calls ...
                         printres=1.0, ntilesx=1, ntilesy=1, tilewidth=100, 
                         basethick=4, zscale=1.0, fileformat="obj", tile_centered=False):
    """ 
    returns a string buffer to in-memory zip file containing 3D model files tiles + a info file
    
    args:
    DEM_name:  name of DEM layer used in Google Earth Engine, see DEM_sources 
    trlat, trlon: lat/lon of top right corner
    bllat, bllon: lat/lon of bottom left corner
    printres: resolution (horizontal) of 3D printer (= size of one pixel) in mm
    ntilesx, ntilesy: number of tiles in x and y
    tilewidth: width of each tile in mm (<- !!!!!), tile height is calculated
    basethick: thickness (in mm) of printed base
    zscale: elevation (vertical scaling)
    fileformat: format of 3D model files: "obj"  = wavefront obj (ascii)
                                          "STLa" = ascii STL 
                                          "STLb" = binary STL
    tile_centered: True-> all tiles are centered around 0/0, False, all tiles "fit together"
    """
    # Sanity checks:
    assert DEM_name in DEM_sources, "Error: DEM must be on of " + str(DEM_sources)
    
    # redirect to logfile written inside the zip file 
    if USE_LOG:
        # redirect stdout to mystdout
        regular_stdout = sys.stdout 
        mystdout = StringIO()
        sys.stdout = mystdout
    
    region = [[bllon, trlat],#WN  NW
              [trlon, trlat],#EN  NE
              [trlon, bllat],#ES  SE
              [bllon, bllat]]#WS  SW    
      
    # get center of region as lon/lat, needed for conversion to meters
    center = [(region[0][0] + region[1][0])/2.0, (region[0][1] + region[2][1])/2.0] 
    
    # Make a more descriptive name for the selected DEM from it's official (ee) name and the center
    # if there's a / (e.g. NOAA/NGDC/ETOPO1), just get the last, ETOPO1
    DEM_title = DEM_name
    if '/' in DEM_name:
        DEM_title = DEM_title.split('/')[-1]
    DEM_title = "%s_%.2f_%.2f" % (DEM_title, center[0], center[1])
    
    print "Log for creating 3D model tile(s) for ", DEM_title
    # print args to log
    args = locals() # dict of local variables
    for k in ("DEM_name", "trlat", "trlon", "bllat", "bllon", "printres", 
                 "ntilesx", "ntilesy", "tilewidth", "basethick", "zscale", "fileformat"):
        print k, "=", args[k]    
    print "process started:", datetime.datetime.now().time().isoformat()
    logging.info("process started: " + datetime.datetime.now().time().isoformat())
        
    print "Region (lat/lon):\n  ", trlat, trlon, "(top right)\n  ", bllat, bllon, "(bottom left)"    
    
    # get UTM zone of center to project into
    utm,h = LatLon_to_UTM(center)
    utm_zone_str = "UTM %d%s" % (utm,h)
    epsg = UTM_zone_to_EPSG_code(utm, h)
    epsg_str = "EPSG:%d" % (epsg)    
    print "center at", center, " UTM",utm, h, ", ", epsg_str
    
    #get extent in meters (depending on lat of center)
    #print "center (lon/lat) at", center
    (latitude_in_m, longitude_in_m) = arcDegr_in_meter(center[1]) # returns: (latitude_in_m, longitude_in_m)
    region_size_in_degrees = [abs(region[0][0]-region[1][0]), abs(region[0][1]-region[2][1]) ]
    region_ratio_for_degrees =  region_size_in_degrees[1] / float(region_size_in_degrees[0])
    region_size_in_meters = [region_size_in_degrees[0] * longitude_in_m, # 0 -> EW, width
                             region_size_in_degrees[1] * latitude_in_m]  # 1 -> NS, height
    region_ratio =  region_size_in_meters[1] / float(region_size_in_meters[0])
   
    print "lon/lat size in degrees:",region_size_in_degrees
    print "x/y size in meters:", region_size_in_meters
    
    # tiling
    num_tiles = [int(ntilesx), int(ntilesy)] # number of tiles in EW (x,long) and NS (y,lat), must be ints
    
    # horizonal resolution of 3D printer
    #print3D_resolution = 0.25 # in mm 
    #print3D_resolution = 0.35
    print3D_resolution = printres
    
    #
    # width/height (in 2D) of 3D model of ONE TILE to be printed, in mm
    #print3D_width_per_tile = 100 # EW
    print3D_width_per_tile = tilewidth
    print3D_height_per_tile = (print3D_width_per_tile * num_tiles[0] * region_ratio) / float(num_tiles[1]) # NS
    
    # width/height of full 3D model (all tiles together)
    print3D_width_total_mm =  print3D_width_per_tile * num_tiles[0] # width => EW 
    print3D_height_total_mm = print3D_width_total_mm * region_ratio   # height => NS 
    
    # number of samples needed to cover ALL tiles
    num_samples_lat = print3D_width_total_mm  / float(print3D_resolution) # width
    num_samples_lon = print3D_height_total_mm / float(print3D_resolution) # height
    print print3D_resolution,"mm print resolution => requested num samples (width x height):", num_samples_lat, "x",  num_samples_lon
    
    # get cell size (in meters) for request from ee # both should be the same
    cell_size_meters_lat = region_size_in_meters[0] / num_samples_lat # width
    cell_size_meters_lon = region_size_in_meters[1] / num_samples_lon # height
    
   
    
    #print  cell_size_meters_lon, "(lon) m,", cell_size_meters_lat, "(lat) m on", DEM_name, "from earth engine"
    # Note: the resolution of the ee raster does not quite match the requested raster at this cell size;
    # it's not bad, req: 1200 x 2235.85 i.e. 19.48 m cells => 1286 x 2282 which is good enough for me.
    # With this, the actually printed cell size is not exactly the print resolution but it's only ~5% off ...
    cell_size = cell_size_meters_lat # will later be used to calc the scale of the model
    print "requesting", cell_size, "m resolution "
    
    # show layer in slippy map
    #ee.mapclient.centerMap(center[0], center[1], 11) 

    

    # Get a download URL for DEM from Earth Engine
    #ee.Initialize(config.EE_CREDENTIALS, config.EE_URL)  
    image1 = ee.Image(DEM_name)
    
    try:
        info = image1.getInfo() # this can go wrong, but we don't really need the info as long as we get the actual data
        print "Google Earth Engine raster:", info["id"],
        print info["properties"]["title"], info["properties"]["link"] # some rasters don't have those properties
    except Exception, e:
        print e
        logging.error("something went wrong with the image info:" + str(e))
        
    request = image1.getDownloadUrl({
        #'scale':90, # cell size
        'scale': cell_size, # cell size in meters
        #'crs': 'EPSG:4326',
        'crs': epsg_str, # projections
        #'region': '[[-120, 35], [-119, 35], [-119, 34], [-120, 34]]',
        'region': str(region), 
        #'format': 'png',
        'format': 'tiff'
        #'format': 'jpeg'
    })
    logging.info("request: " + request)

    #ee.mapclient.addToMap(image1, {'min': 0, 'max': 4000})
    #PALETTE = ['000000', 'ff0000','ffff00', 'ffffff']
    #ee.mapclient.addToMap(image1, {'min':0, 'max':3000, 'gamma': 1.5})
    #                               'palette': PALETTE
    #                               })
    
    # CH: not sure about this loop
    web_sock = None
    to = 2
    while web_sock == None:
        #
        # download zipfile from url
        #
        try: 
            web_sock = urllib2.urlopen(request, timeout=20) # 20 sec timeout
        except socket.timeout, e:
            raise logging.error("Timeout error %r" % e)        
        except urllib2.HTTPError, e:
            logging.error('HTTPError = ' + str(e.code)) 
            if e.code == 429:  # 429: quota reached
                time.sleep(random.randint(1,10)) # wait for a couple of secs
        except urllib2.URLError, e:
            logging.error('URLError = ' + str(e.reason))
        except httplib.HTTPException, e:
            logging.error('HTTPException')
        except Exception:
            import traceback
            logging.error('generic exception: ' + traceback.format_exc())    
        
        # at any exception wait for a couple of secs
        if web_sock == None:
            time.sleep(random.randint(1,10)) 
       
    # read the zipped folder into memory    
    buf = web_sock.read()
    memfile = io.BytesIO(buf)
    memfile.flush() # not sure if this is needed ...
    #print memfile
    
    # pretend we've got a .zip folder (it's just in memory instead disk) and read the tif inside
    zipdir = zipfile.ZipFile(memfile)

     
    # Debug: unzip both files into a folder
    #zipdir.extractall(zipfile.namelist()[1][:-3])
    
    # get the tif file from the zip
    nl = zipdir.namelist()
    tifl = [f for f in nl if f[-4:] == ".tif"]
    assert tifl != [], "zip from ee didn't contain a tif: " +  str(nl)   
    
    if DEM_name == """NOAA/NGDC/ETOPO1""":  # will have bedrock and ice_surface tifs
         tif = [f for f in tifl if "ice_surface" in f][0]   
    else:
        tif = tifl[0]
    

    #worldfile = zipdir.read(zipfile.namelist()[0]) # world file as textfile 
    #raster_info = [float(l) for l in worldfile.splitlines()]  # https://en.wikipedia.org/wiki/World_file
    str_data = zipdir.read(tif)
    print "d/l geotiff size:", len(str_data) / 1048576.0, "Mb"
    imgdata = io.BytesIO(str_data) # PIL doesn't want to read the raw string from unzip
    memfile.close()
    
    # delete zipped file (worth it?)
    del zipdir, str_data
    
    # open tif with PIL
    PILimg = Image.open(imgdata) # I;16s
    print PILimg
    
    # PIL cannot deal with mode I;16s i.e. 16 bit signed integers, and throws ValueError: unrecognized mode http://stackoverflow.com/questions/7247371/python-and-16-bit-tiff
    if PILimg.mode == "I;16S": # HACK for integer DEMs (GMTED2010/SRTM and ETOPO1)
        PILimg.mode = "I"  # with this hack, getdata(), etc. works!  
        #npim = numpy.asarray(PILimg, dtype=numpy.int16) # force the type to int16
        npim = numpy.asarray(PILimg, dtype=numpy.int16).astype(numpy.float32) # force to 32-bit float
        min_elev = npim.min(); max_elev = npim.max()
        if DEM_name == """USGS/GMTED2010""":
            npim = npim.clip(0) #set negative values to 0, GMTED2010 uses -32768 for offshore 
    else:
        npim = numpy.asarray(PILimg).astype(numpy.float32) # make a numpy array from PIL image # TODO(?): use float32? 64?
    #print npim, npim.shape, npim.dtype
    print "full raster (height,width): ", npim.shape, npim.dtype  
    
    # get horizontal map scale (1:x) so we know how to scale the elevation later
    print3D_scale_number =  (npim.shape[1] * cell_size) / (print3D_width_total_mm / 1000.0) # map scale ratio (mm -> m)
    print "map scale is 1 :", print3D_scale_number # EW scale
    #print (npim.shape[0] * cell_size) / (print3D_height_total_mm / 1000.0) # NS scale
    print3D_resolution_adjusted = print3D_width_total_mm / float(npim.shape[1]) # adjusted print resolution
    #print print3D_height_total_mm / float(npim.shape[0])
    print "Actual 3D print resolution (1 cell):", print3D_resolution_adjusted, "mm"
    
    # Adjust raster to nice multiples of tiles. If needed, crop raster from right and bottom
    remx = npim.shape[1] % num_tiles[0] 
    remy = npim.shape[0] % num_tiles[1]
    if remx > 0 or  remy > 0:
        print "Cropping for %d (width) x %d (height) tiles, rem. is x=%d, y=%d" % (num_tiles[0], num_tiles[1], remx, remy),
        npim = npim[0:npim.shape[0]-remy, 0:npim.shape[1]-remx]
        print " -> cropped to (y,x):",npim.shape
    
       
    
    # min/max elev (all tiles)
    print "elev min/max : %.2f to %.2f" % (numpy.nanmin(npim), numpy.nanmax(npim)) # use nanmin() so we can use (NaN) undefs 
    
    # apply z-scaling
    if zscale != 1.0:
        npim *= zscale
        print "elev min/max after x%.2f z-scaling: %.2f to %.2f" % (zscale, numpy.nanmin(npim), numpy.nanmax(npim))
    
    """  
    # plot dem 
    import matplotlib.pyplot as plt
    plt.ion()
    fig = plt.figure(figsize=(7,10))
    imgplot = plt.imshow(npim, aspect=u"equal", interpolation=u"none")
    cmap_name = 'gist_earth' # gist_earth or terrain or nipy_spectral
    imgplot.set_cmap(cmap_name) 
    #a = fig.add_axes()
    plt.title(DEM_name + " " + str(center))
    plt.colorbar(orientation="horizontal")
    fig.canvas.draw()
    plot_buf = fig.canvas.tostring_rgb()
    
    # histogram with colored bars
    rng = (npim.flatten().min(), npim.flatten().max())
    numcols = imgplot.cmap.N
    cmap = imgplot.cmap(numpy.arange(numcols)) # will only have 256 colors
    mult = 1/4.0
    numbins = numcols * mult
    bns = numpy.linspace(rng[0], rng[1], num=numbins) # => i/4 when setting colors!
    fig = plt.figure(figsize=(8,4))
    n,bins,patches = plt.hist(npim.flatten(),bins=bns, lw=0)
    maxy=max(n)
    #xext = (rng[1]-rng[0])
    #asp = xext/maxy # current aspect
    #a.set_aspect(asp/4) # make smaller in y
    for i in range(numpy.size(patches)): # set each bar's color
            plt.setp(patches[i], color=cmap[int(i/mult)])
    plt.tight_layout()
    plt.show()
    """
  
    
    # create tile info dict
    DEM_name = DEM_name.replace("/","-") # replace / as it could be paret of a filename
    tile_info = { 
        "DEMname": DEM_name, # name of raster requested from earth eng.
        "crs" : epsg_str, # EPSG name for UTM zone
        "UTMzone" : utm_zone_str, # UTM zone eg  UTM13N
        "scale"  : print3D_scale_number, # horizontal scale number, defines the size of the model (= 3D map): eg 1000 means 1m in model = 1000m in reality
        "pixel_mm" : print3D_resolution_adjusted, # lateral (x/y) size of a pixel in mm
        "max_elev" : numpy.nanmax(npim), # tilewide minimum/maximum elevation (in meter)
        "min_elev" : numpy.nanmin(npim),
        "z_scale" :  zscale,     # z (vertical) scale (elevation exageration) factor
        "base_thickness_mm" : basethick, 
        "bottom_relief_mm": 1.0,  # thickness of the bottom relief image (float), must be less than base_thickness
        "folder_name": DEM_title,  # folder/zip file name for all tiles 
        "tile_centered" : tile_centered, # True: each tile's center is 0/0, False: global (all-tile) 0/0
        "tile_no_x": -1, # current(!) tile number along x
        "tile_no_y": -1, # current(!) tile number along y
        "full_raster_width": -1,
        "full_raster_height": -1,
        #"magnet_hole_mm": (8, 3.0) # hole for holding magnet diameter and height, height has to be a bit more than base thickness!
    }    
    
    #
    # Make tiles (subsets) of the full raster and generate 3D grid model   
    #
    
    # num_tiles[0], num_tiles[1]: x, y !
    cells_per_tile_x = npim.shape[1] / num_tiles[0] # tile size in pixels
    cells_per_tile_y = npim.shape[0] / num_tiles[1]
    print "Cells per tile (x/y)", cells_per_tile_x, "x", cells_per_tile_y
    
    
    # pad full raster by one at the fringes
    npim = numpy.pad(npim, (1,1), 'edge') # will duplicate edges, including nan # won't work on GAE's 1.6.1!

    
    # store size of full raster
    tile_info["full_raster_height"], tile_info["full_raster_width"]  = npim.shape
    #print tile_info
    
    imz = InMemoryZip() # in-memory zip file, will be filled with tiles and returned for download
    
    """
    # Read in logo file (must be 8 bit)
    logo = Image.open("logo_8bit_gfl5.png")
    if logo.getbands()[0] != "L": print "warning: logo image band 1 is not L, taking band 1 ... let's hope that works"
    bands = logo.split() # splits into bands, even if there's only 1
    logo = bands[0] # should ensure that logo is a single band image of mode L
    """
    
    # within the padded full raster, grab tiles - but each with a 1 cell fringe!
    for tx in range(num_tiles[0]):
        for ty in range(num_tiles[1]):
            tile_info["tile_no_x"] = tx+1
            tile_info["tile_no_y"] = ty+1
            print "\ntile", tx+1,"/", ty+1, "of", num_tiles[0], "/", num_tiles[1]
            
            # extract current tile from full raster
            start_x = tx * cells_per_tile_x 
            end_x = start_x + cells_per_tile_x + 1 + 1
            start_y = ty * cells_per_tile_y 
            end_y = start_y + cells_per_tile_y + 1 + 1      
            # as STL will use float32 anyway, I cast it here to float32 instead of float (=64 bit)
            tile_elev_raster = npim[start_y:end_y, start_x:end_x].astype(numpy.float32) #  [y,x]
            
            
            """
               
            # CH: as it turns out, having anything but a flat bottom is bad for printing, but I'll leave the code
            # for setting a relief into the bottom raster here for now ...
                
            #
            # make bottom image
            #
            ncols, nrows = tile_elev_raster.shape[::-1] #width/height (shape) needs to be flipped as numpy is [y,x]
            bimg = Image.new("L", (ncols, nrows), color=0) # black 8 bit image (0-255)
            
            # put a 1 cell wide groove at the fringe
            draw = ImageDraw.Draw(bimg)
            fos = 2 # fringe offset
            draw.rectangle([(fos, fos),  (ncols-fos, nrows-fos)], outline=64, fill=None) 
            #bimg.show()
    
            #
            # put placement diagram in center
            #
            '''
            if num_tiles[0] * num_tiles[1] > 1: # not needed if there's only one tile ...
                w2,h2,w3,h3  = (ncols/2.0, nrows/2.0, ncols/3.0, nrows/3.0) # halfs and thirds
                # for each tile, draw a small empty box, for the current tile (tx,ty), fill the box
                sbw, sbh = w3 / num_tiles[0], h3 / num_tiles[0] # width, height  of small box
                for tnx in range(num_tiles[0]):
                    for tny in range(num_tiles[1]):    
                        ul = (w3 + sbw * tnx, h3 + sbh * tny)
                        lr = (ul[0] + sbw, ul[1] + sbh)
                        if tnx == tx and tny == ty: # tx/ty is the current tile index
                            draw.rectangle([ul, lr], fill=255) 
                        else:
                            draw.rectangle([ul, lr], outline=64, fill=None) 
            bimg.show()        
            '''
        
            
            
            
            # draw text
            fnt = ImageFont.truetype('Consolab.ttf', min(ncols,nrows)/12) # locally installed ttf file (same folder as this .py file). Use a monospaced font!
            #fnt= ImageFont.load_default() # seems to be  10 pixels high
            #lh = 14
            fw,fh = fnt.getsize("X") # font height and font width, if monospaces it's the same for all chars
            fh = fh * 1.5 # account for space between lines
            
            y = nrows / 6 # 
            x = ncols / 5 # 
            
            def text(txt,y,x): # local function, cause I'm lazy
                draw.text((x,y), txt, fill=255, font=fnt)
                return y + fh # simlulate line feed, y will already be at next line
            
            
            #lh = text("tile %d/%d" % (tile_info["tile_no_x"],tile_info["tile_no_y"]), lh)
            #dts = datetime.datetime.strftime(datetime.datetime.now(),'%Y-%m-%d')
            #lh = text(tile_info["DEMname"]+" "+tile_info["UTMzone"], lh)
            y = text("tile %d %d" % (tile_info["tile_no_x"],tile_info["tile_no_y"]), y,x)
            y = text("GeoFabLab", y, x)
            y = text("res %.2f mm" % (tile_info["pixel_mm"]), y, x)
            y = text("|", y, x)
            y = text("N", y, x)
            y = text("1:{:,}".format(int(tile_info["scale"])), y, x)
            y = text("zsc %.2f" % (tile_info["z_scale"]), y, x)
            y = text("%s" % (tile_info["UTMzone"]), y, x)
            

            
            #small_logo = logo.copy() 
            #tn_size = min(tile_elev_raster.shape) / 2
            #small_logo.thumbnail((tn_size, tn_size)) # changes small_logo internally, will preserve aspect
            #upper_left_corner = (tile_elev_raster.shape[1]/2-3, tile_elev_raster.shape[0]/2-3)
            #bimg.paste(small_logo, upper_left_corner)  # paste into center
            #bimg.show()
            
    
            #bimg.show()
            bimg = bimg.transpose(Image.FLIP_LEFT_RIGHT) # text needs to be flipped, but seemsto screw up draw !
            #bimg.show()    
            
            # Convert bimg to numpy, normalize and scale
            bottom_elev = numpy.asarray(bimg).astype(numpy.float)
            #print bottom_elev.shape, bottom_elev.min(), bottom_elev.max()
            bottom_elev /= 255.0 # normalize from max 8 bit value (255) to 1.0
            #print bottom_elev.min(), bottom_elev.max() # normalized to 0 - 1.0
            bottom_elev *= tile_info["bottom_relief_mm"]   # scale       
            
            #
            # Make holes for magnets "magnet_hole_mm" -> (diameter, height)
            #
            if tile_info["magnet_hole_mm"][1] > tile_info["base_thickness_mm"]:
                print "Warning: base thickness", tile_info["base_thickness_mm"], "is not thicker than height of magnet hole", tile_info["magnet_hole_mm"][1]
            himg = Image.new("L", (ncols, nrows), color=0) # binary "mask"
            draw = ImageDraw.Draw(himg) # draw into it
            dia = math.ceil(tile_info["magnet_hole_mm"][0] / tile_info["pixel_mm"]) # diameter in px
            ofs_mm = 4.0 # offset from border in mm, same in x and y
            of = math.ceil(ofs_mm / tile_info["pixel_mm"])# offset in pixels 
    
            bb_list = [ # list of bounding boxes (ulx,uly, lrx,lry) to draw a hole into
                (of, of, of+dia, of+dia), # ul
                (ncols-of-dia, of, ncols-of, of+dia), # ur 
                (of, nrows-of-dia, of+dia, nrows-of), # ll
                (ncols-of-dia, nrows-of-dia, ncols-of, nrows-of), # lr
                (ncols/2-dia/2, nrows/2-dia/2, ncols/2+dia/2, nrows/2+dia/2), # center
                ]
    
            for bb in bb_list:
                #print bb
                draw.ellipse(bb, fill=255, outline=255)

            #print "Creating", len(bb_list), "holes for magnets, diameter:", tile_info["magnet_hole_mm"][0], "mm, height", tile_info["magnet_hole_mm"][1], "mm"
            #himg.show()

            # Convert himg to numpy and mult by hole height 
            hole_elev = numpy.asarray(himg).astype(numpy.float)
            hole_elev /= hole_elev.max() # normalize to 0 - 1.0
            hole_thickness = tile_info["magnet_hole_mm"][1]
            hole_elev *= hole_thickness # mult 1 (=hole) by height
            #print hole_elev.shape, hole_elev.min(), hole_elev.max()
            
            # integrate into bottom_elev array
            bottom_elev = numpy.maximum(bottom_elev, hole_elev)
            #print bottom_elev.min(), bottom_elev.max()
                  
            # test: set an internal region to Nan
            #for n in range(20,25):
                    #tile_elev_raster[n,n] = numpy.nan
                    #tile_elev_raster[n,n-1] = numpy.nan
                    #tile_elev_raster[n-1,n] = numpy.nan
            
            """
            
            #
            # Make a 3D model from top and bottom
            #
            #g = grid(tile_elev_raster, bottom_elev, tile_info)
            g = grid(tile_elev_raster, None, tile_info) # Bottom = 0
            
            # convert 3D model to a (in-memory) file (buffer)
            if  fileformat == "obj":
                b = g.make_OBJfile_buffer()
                # TODO: add a header for obj
            elif fileformat == "STLa":
                b = g.make_STLfile_buffer(ascii=True)
            elif fileformat == "STLb":
                b = g.make_STLfile_buffer(ascii=False)
            print "size:", len(b) / float(1024*1024), "Mb"
            
            # append buffer to in-memory zip
            tn = DEM_title+"_tile_%d_%d" % (tile_info["tile_no_x"],tile_info["tile_no_y"]) + "." + fileformat[:3] # name of file insize zip
            imz.append(tn, b) 
            
            """
            # write tile to disk
            fname = "tmp" + os.sep + tile_info["folder_name"] + "_%d-%d.obj" % (tx+1,ty+1)
            f = open(fname, 'wb+')
            f.write(b)
            f.close()        
            print "wrote tile into", fname
            """
            
    
    print "\nfinished:", datetime.datetime.now().time().isoformat()
    logging.info("process finished: " + datetime.datetime.now().time().isoformat())
    
    # put logfile into zip 
    if USE_LOG:
        sys.stdout = regular_stdout 
        logstr = mystdout.getvalue()
        lines = logstr.splitlines()
        logstr = u"\r\n".join(lines) #otherwise notpad doesn't do linebreaks (vim does)
        imz.append(tile_info["folder_name"] + "_log.txt", logstr)
    print "done"
    
    # return string buffer
    return imz.get_string_buffer()
    
# MAIN - I left this in, in case I need to run it outside the server
if __name__ == "__main__":
    
    # Grand Canyon, Franek -109.947664, 37.173425  -> -109.905481, 37.151472
    imz = get_zipped_tiles("USGS/NED", 44.7220, -108.2886, 44.47764, -107.9453, ntilesx=2, ntilesy=2)
    imz.writetofile("DEM_test.zip")
    print "done"

    
