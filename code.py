import time
import board
import terminalio
import displayio
import terminalio
import gc
import json
import wifi
import adafruit_requests
import adafruit_connection_manager
import adafruit_display_text.label
from bitmaptools import arrayblit
from adafruit_portalbase.network import HttpError
from adafruit_matrixportal.matrixportal import MatrixPortal
from adafruit_matrixportal.network import Network
from adafruit_display_text.label import Label
from microcontroller import watchdog
from watchdog import WatchDogMode
from secrets import secrets

print('Starting up');

# 
# Settings
# 
# Flight display settings
ROW_ONE_COLOUR      = 0xEE82EE # Grey
ROW_TWO_COLOUR      = 0x004B00 # Green
ROW_THREE_COLOUR    = 0xFFA500 # Yellow
PLANE_COLOUR        = 0x4B0082 # Purple
PAUSE_BETWEEN_LABEL_SCROLLING   = 1 # Time in seconds to wait between scrolling one label and the next
NO_FLIGHT_DISPLAY_CLEAR_DELAY   = 60 # Time after the last flight was found before switching back to clock
PLANE_SPEED = 0.03 # speed plane animation will move - pause time per pixel shift in seconds
TEXT_SPEED  = 0.03 # speed text labels will move - pause time per pixel shift in seconds
# Clock settings
CLOCK_BLINK = False    # Blink the cursor between minute/hour/seconds or not
TIME_COLOUR = 0xEE82EE # Grey
DATE_COLOUR = 0xEE82EE # Grey

# Set up watchdog which will reset the device if not fed (see watchdog.feed() calls)
# Should auto recover from freezing up
#watchdog.timeout=16 # timeout in seconds
#watchdog.mode = WatchDogMode.RESET

QUERY_DELAY = 30 # How often to query fr24, in seconds
BOUNDS_BOX  = secrets["bounds_box"] # Area to search for flights, see secrets file

# 
# Set up URL prefixes for using fr24 data
# 
FLIGHT_SEARCH_HEAD="https://data-cloud.flightradar24.com/zones/fcgi/feed.js?bounds="
FLIGHT_SEARCH_TAIL="&faa=1&satellite=1&mlat=1&flarm=1&adsb=1&gnd=0&air=1&vehicles=0&estimated=0&maxage=14400&gliders=0&stats=0&ems=1&limit=1"
FLIGHT_SEARCH_URL=FLIGHT_SEARCH_HEAD+BOUNDS_BOX+FLIGHT_SEARCH_TAIL
FLIGHT_LONG_DETAILS_HEAD="https://data-live.flightradar24.com/clickhandler/?flight="
# Request headers for fr24 requests
rheaders = {
     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:106.0) Gecko/20100101 Firefox/106.0",
     "cache-control": "no-store, no-cache, must-revalidate, post-check=0, pre-check=0",
     "accept": "application/json"
}
# Limit how much memory can be used by json parsing
json_size=14336
json_bytes=bytearray(json_size)


# 
# Set up core components
# 
matrixportal = MatrixPortal(status_neopixel=None, debug=False)
network = Network(status_neopixel=board.NEOPIXEL, debug=False)
# Set up the HTTP request library
pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
ssl_context = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)
requests = adafruit_requests.Session(pool, ssl_context)


#
# Set up plane pixel drawing - flies across screen when flight is found
# 
plane_bitmap = displayio.Bitmap(12, 12, 2)
# Squint and it looks like a plane
plane_data = bytes([
    0,0,0,0,0,0,0,0,0,0,0,0,    
    0,0,0,0,0,1,0,0,0,0,0,0,
    0,0,0,0,1,1,0,0,0,0,0,0,
    0,0,0,1,1,1,0,0,0,0,0,0,
    0,0,1,1,1,0,0,0,1,0,0,0,
    1,1,1,1,1,1,1,1,1,0,0,0,
    1,1,1,1,1,1,1,1,1,0,0,0,
    0,0,1,1,1,0,0,0,1,0,0,0,
    0,0,0,1,1,1,0,0,0,0,0,0,
    0,0,0,0,1,1,0,0,0,0,0,0,
    0,0,0,0,0,1,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,
    ])
arrayblit(plane_bitmap, plane_data, x1=0, y1=0, x2=12, y2=12)
plane_palette = displayio.Palette(2)
plane_palette[1] = PLANE_COLOUR
plane_palette[0] = 0x000000
plane_tilegrid = displayio.TileGrid(plane_bitmap, pixel_shader = plane_palette)
plane_animation_group = displayio.Group(x = matrixportal.display.width + 12, y = 10)
plane_animation_group.append(plane_tilegrid)


# 
# Set up flight detail display 
# 
flight_labels = [
    Label(
        terminalio.FONT,
        color=ROW_ONE_COLOUR,
        text=""
    ),
    Label(
        terminalio.FONT,
        color=ROW_TWO_COLOUR,
        text=""
    ),
    Label(
        terminalio.FONT,
        color=ROW_THREE_COLOUR,
        text=""
    )
]
flight_labels[0].x = 1
flight_labels[0].y = 4
flight_labels[1].x = 1
flight_labels[1].y = 15
flight_labels[2].x = 1
flight_labels[2].y = 25
flight_labels_text = ["","",""]

# Add the labels to the display
flight_label_group = displayio.Group()
for label in flight_labels:
    flight_label_group.append(label)
matrixportal.display.root_group = flight_label_group


# 
# Set up Clock display
# 
clock_group = displayio.Group()  # Create a Group
clock_bitmap = displayio.Bitmap(64, 32, 2)  # Create a bitmap object,width, height, bit depth
clock_color = displayio.Palette(3)  # Create a color palette
clock_color[0] = 0x000000  # black background
clock_color[1] = TIME_COLOUR
clock_color[2] = DATE_COLOUR

# Create a TileGrid using the Bitmap and Palette
clock_tile_grid = displayio.TileGrid(clock_bitmap, pixel_shader=clock_color)
clock_group.append(clock_tile_grid)  # Add the TileGrid to the Group
clock_time_label = Label(terminalio.FONT)
clock_group.append(clock_time_label)
clock_date_label = Label(terminalio.FONT)
clock_group.append(clock_date_label)


# 
# Function to scroll the plane animation across the screen
# 
def plane_animation():
    matrixportal.display.root_group = plane_animation_group
    for i in range(matrixportal.display.width+24,-12,-1):
            plane_animation_group.x=i
            watchdog.feed()
            time.sleep(PLANE_SPEED)

# 
# Function to scroll a label, start at the right edge of the screen and go left one pixel at a time
# 
def scroll(line):
    line.x=matrixportal.display.width
    for i in range(matrixportal.display.width+1,0-line.bounding_box[2],-1):
        line.x=i
        watchdog.feed()
        time.sleep(TEXT_SPEED)

# Populate the labels, then scroll longer versions of the text
def display_flight():
    # Immediately show all labels as is
    matrixportal.display.root_group = flight_label_group
    for index,label in enumerate(flight_labels):
        label.text = flight_labels_text[index]
    time.sleep(PAUSE_BETWEEN_LABEL_SCROLLING)

    # Now scroll each label in turn
    for index,label in enumerate(flight_labels):
        label.x=matrixportal.display.width+1
        scroll(label)
        label.x=1
        time.sleep(PAUSE_BETWEEN_LABEL_SCROLLING)


# 
# Function to blank the flight detail text
# 
def clear_flight():
    for label in flight_labels:
        label.text = ""


#
# Take the flight number we found with a search, and load details about it
# 
def get_flight_details(flight_number):
    # the JSON from FR24 is too big for the matrixportal memory to handle. So we load it in chunks into our static array,
    # as far as the big "trails" section of waypoints at the end of it, then ignore most of that part. Should be about 9KB, we have 14K before we run out of room..
    global json_bytes
    global json_size
    byte_counter = 0
    chunk_length = 1024
    success = False

    # zero out any old data in the byte array
    for i in range(0, json_size):
        json_bytes[i] = 0

    # Get the URL response one chunk at a time
    try:
        response = requests.get(url = FLIGHT_LONG_DETAILS_HEAD + flight_number, headers = rheaders)
        for chunk in response.iter_content(chunk_size = chunk_length):

            # if the chunk will fit in the byte array, add it
            if(byte_counter+chunk_length <= json_size):
                for i in range(0, len(chunk)):
                    json_bytes[i+byte_counter] = chunk[i]
            else:
                print("Exceeded max string size while parsing JSON")
                return False

            # check if this chunk contains the "trail:" tag which is the last bit we care about
            trail_start = json_bytes.find((b"\"trail\":"))
            byte_counter += len(chunk)

            # if it does, find the first/most recent of the many trail entries, giving us things like speed and heading
            if not trail_start == -1:
                # work out the location of the first } character after the "trail:" tag, giving us the first entry
                trail_end = json_bytes[trail_start:].find((b"}"))
                if not trail_end == -1:
                    trail_end += trail_start
                    # characters to add to make the whole JSON object valid, since we're cutting off the end
                    closing_bytes = b'}]}'
                    for i in range (0, len(closing_bytes)):
                        json_bytes[trail_end + i] = closing_bytes[i]
                    # zero out the rest
                    for i in range(trail_end + 3, json_size):
                        json_bytes[i] = 0
                    # Stop reading chunks
                    print("Details lookup saved " + str(trail_end) + " bytes.")
                    return True
                    
    # Handle occasional URL fetching errors
    except (RuntimeError, OSError, HttpError) as e:
            print("Error--------------------------------------------------")
            print(e)
            return False

    #If we got here we got through all the JSON without finding the right trail entries
    print("Failed to find a valid trail entry in JSON")
    return False


#
# Function to extract the relevant fields from the json data
# 
def parse_details_json():
    global json_bytes
    try:
        # get the JSON from the bytes
        long_json=json.loads(json_bytes)

        # Extract fields from the JSON, handle any non-existent keys and set 'Unknown' as default
        flight_number               = long_json.get('identification', {}).get('number', {}).get('default', 'Unknown')
        flight_callsign             = long_json.get('identification', {}).get('callsign', 'Unknown')
        aircraft_code               = long_json.get('aircraft', {}).get('model', {}).get('code', 'Unknown')
        aircraft_model              = long_json.get('aircraft', {}).get('model', {}).get('text', 'Unknown')
        airline_name                = long_json.get('airline', {}).get('name', 'Unknown')
        airport_origin_name         = long_json.get('airport', {}).get('origin', {}).get('name', 'Unknown')
        airport_origin_code         = long_json.get('airport', {}).get('origin', {}).get('code', {}).get('iata', 'Unknown')
        airport_destination_name    = long_json.get('airport', {}).get('destination', {}).get('name', 'Unknown')
        airport_destination_code    = long_json.get('airport', {}).get('destination', {}).get('code', {}).get('iata', 'Unknown')
        
        # Remove airport from airport names
        airport_origin_name = airport_origin_name.replace(" Airport","")
        airport_destination_name=airport_destination_name.replace(" Airport","")

        # Use global so we can change these values inside this function, the values are read again in display_flight()
        global flight_labels_text
        flight_labels_text = ["","",""]
        flight_labels_text[0] = flight_callsign + "-" + flight_number + " - " + airline_name
        flight_labels_text[1] = airport_origin_code + "-" + airport_destination_code + " - " + airport_origin_name + "-" + airport_destination_name
        flight_labels_text[2] = aircraft_code + " - " + aircraft_model

        # optional filter example - check things and return false if you want

        # if altitude > 10000:
        #    print("Altitude Filter matched so don't display anything")
        #    return False

    except (KeyError, ValueError,TypeError) as e:
        print("JSON error")
        print (e)
        return False


    return True

# 
# Function to find flights within a bounds box
# 
def get_flights():
    matrixportal.url = FLIGHT_SEARCH_URL
    try:
        response=requests.get(url=FLIGHT_SEARCH_URL,headers=rheaders).json()
    except (RuntimeError,OSError, HttpError, ValueError, adafruit_requests.OutOfRetries) as e:
        print(e.__class__.__name__ + "--------------------------------------")
        print(e)
        return False
    if len(response) == 3:
        for flight_id, flight_info in response.items():
            # the JSON has three main fields, we want the one that's a flight ID
            if not (flight_id == "version" or flight_id == "full_count"):
                if len(flight_info) > 13:
                    return flight_id
    else:
        return False

# 
# Function to update the clock time/date data
# 
def update_clock(*, hours=None, minutes=None, show_colon=False):
    now = time.localtime()  # Get the time values we need

    clock_time_label.color = TIME_COLOUR
    clock_date_label.color = DATE_COLOUR

    if CLOCK_BLINK:
        colon = ":" if show_colon or now[5] % 2 else " "
    else:
        colon = ":"

    # Update the text for the time
    clock_time_label.text = "{hours}{colon}{minutes:02d}{colon}{seconds:02d}".format(
        hours=now[3], minutes=now[4], seconds=now[5], colon=colon
    )
    # Center the label by getting the box width and removing it from the width of the display
    time_box_x, time_box_y, time_box_width, time_box_height = clock_time_label.bounding_box
    clock_time_label.x = round(matrixportal.display.width / 2 - time_box_width / 2)
    # Put it 1/4ths down the height of the display
    clock_time_label.y = (matrixportal.display.height // 4) * 1
    
    # Update the text for the date
    clock_date_label.text = "{day:02d}/{month:02d}/{year}".format(
        day=now[2],month=now[1],year=now[0]
    )
    # Center the label by getting the box width and removing it from the width of the display
    date_box_x, date_box_y, date_box_width, date_box_h = clock_date_label.bounding_box
    clock_date_label.x = round(matrixportal.display.width / 2 - date_box_width / 2)
    # Put it 3/4ths down the height of the display
    clock_date_label.y = (matrixportal.display.height // 4) * 3
    
    # Set as the main display
    matrixportal.display.root_group = clock_group
  
# 
# Set some defaults to start and run the main loop
# 
last_flight = ''         # Used to keep a record of the last flight detected
last_flight_detected = 0 # Timestamp of when the last flight was detected
last_flight_check = 0    # Timestamp of when we last checked for overhead flights
last_time_sync = 0       # Timestamp of when we last synced the clock with the internet
while True:
    watchdog.feed()
    
    # Sync the time with the internet every hour
    if time.monotonic() > last_time_sync + 3600:
        print("Synchronising time")
        network.get_local_time()
        last_time_sync = time.monotonic()

    # Get current flights if enough time has passed since the last check
    if time.monotonic() > (last_flight_check + QUERY_DELAY):
        print("Checking for flights")
        flight_id = get_flights()
        last_flight_check = time.monotonic()
        watchdog.feed()
        # If flight is returned - show it
        if flight_id:
            if flight_id == last_flight:
                print("Same flight found, so keep showing it")
                display_flight()
            else:
                print("New flight " + flight_id + " found, clear display")
                clear_flight()
                # Retrieve more details about this flight
                if get_flight_details(flight_id):
                    watchdog.feed()
                    gc.collect()
                    # Try to parse the json returned
                    if parse_details_json():
                        # If successful show the animation and flight details
                        gc.collect()
                        plane_animation()
                        display_flight()
                    else:
                        print("error parsing JSON, skip displaying this flight")
                else:
                    print("error loading details, skip displaying this flight")
                # Record the last flight we found and when
                last_flight = flight_id
                last_flight_detected = time.monotonic()
                
    # Clear the display X seconds after the last flight was found and show the clock
    if time.monotonic() - last_flight_detected > NO_FLIGHT_DISPLAY_CLEAR_DELAY:
        clear_flight()
        update_clock()
    # If time isn't up yet and we did find a flight before, keep showing it
    elif flight_id:
        display_flight()

    # Sleep for 1 second before doing the loop again, feed the watchdog and collect the rubbish
    time.sleep(1)
    watchdog.feed()
    gc.collect()
