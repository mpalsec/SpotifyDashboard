import neo4j
import streamlit as st
import pandas as pd
from neo4j import GraphDatabase, exceptions
from Spotify2DBScript import Neo4jHelper,API2DB,apiHelper
import datetime
from datetime import timezone, date, timedelta,datetime, time
import math
import random
import matplotlib.pyplot as plt
import altair as alt
import bcrypt
from pymongo import MongoClient
import docker
import string
import uuid
import secrets
import base64

# initialize Neo4jHelper for making db queries, along w/ an apiHelper for spotify API calls and docker for creating new neo4j DBs
neo4jManager = Neo4jHelper(user_uid="")
apiManager = apiHelper()

def nav_to_auth_url(auth_url):
    nav_script = """
        <meta http-equiv="refresh" content="0; url='%s'">
    """ % (auth_url)
    st.write(nav_script, unsafe_allow_html=True)

# function converts timezoneless datetime object into a UTC timestamp based on the users current timezone
def convert_to_utc_timestamp(date_obj):
    datetime_obj = datetime.combine(date_obj, time.min)
    return datetime_obj.replace(tzinfo=timezone.utc).timestamp()

def generate_password(length=12):
    # Allowed special characters (excluding \ / > < | )
    allowed_special = "!@#$%^&*()-_=+[]{};:'\",.?~`"

    # Define the character pool
    all_chars = string.ascii_letters + string.digits + allowed_special

    while True:
        password = "".join(random.choice(all_chars) for _ in range(length))

        # Ensure at least one special character (if required)
        if any(char in allowed_special for char in password):
            return password

###################################################################### Mongo User DB Functions #####################################################################
# genereates a secure state parameter to use during oauth 
def generate_state_parameter(length=32):
    # Generate a random byte string
    random_bytes = secrets.token_bytes(length)
    # Encode as URL-safe base64 and remove trailing '=' padding
    state = base64.urlsafe_b64encode(random_bytes).rstrip(b'=').decode('ascii')
    return state

# runs a mongo query. query type can either be "find_one" (look for one entry), "find" (look for multiple entries), or "delete" (delete entries based on query), 
# and is used to determine the type of query that will be run. For the "create" query_type, the query is the document that will be added to the database.
# projection is used in conjunction with the "find" or "find_one" queries, and is a dictionary that outlines which fields should be returned from the document
def run_query(query, query_type, database_name, collection_name, update={}, projection={}):
    try:   
        client = MongoClient(f"""mongodb://{st.secrets["user_database"]["username"]}:{st.secrets["user_database"]["password"]}@localhost:27017/userDB""")
        db = client[database_name]
        collection = db[collection_name]

        if query_type == "find":
            print("in find")
            if projection == {}:
                result = list(collection.find(query))
            else:
                result = list(collection.find(query, projection))

        elif query_type == "delete":
            # delete document
            result = collection.delete_many(query)

            if result.deleted_count > 0:
                print("Successfully Deleted!")
                result = True
            else:
                print("no matching document found to delete")
                result = False

        elif query_type == "create":
            result = list(collection.insert_one(query))

        elif query_type == "update":
            #print(f"update: {update}")
            result_not_passed = collection.update_one(query, {"$set": update})

            result = list(collection.find(query))

        else:
            print("Error: invalid query_type")
            return {}
        
        client.close()
        return result[0]
    
    except Exception as e:
        print(f"An Error has occured while making Mongo Query: {e}")
        return {}

# checks to see if password provided matches the hash in database
def validate_password(email, password):
    input_password = password.encode('utf-8')
    result = run_query({"email": email},"find","userDB","listings",projection={"password_hash":1, "_id":0})
    if not result:
        print("Error: email does not exist, please try a different email or sign up for an account")
        return "noUser"
    else:
        print(f"result: {result}")
        if bcrypt.checkpw(input_password, result['password_hash']):
            print("Passwords Match")
            return "loggedIn"
        else:
            print("Passwords Don't Match")
            return "passwordMismatch"

# function deletes a user based on their uid. Used if someone wants to delete account
def delete_user(user_uid):
    result = run_query({"user_uuid": user_uid}, "delete", st.secrets["user_database"]["database_name"], st.secrets["user_database"]["collection_name"])

    # delete all nodes in DB associated with user
    neo4j_result = neo4jManager.deleteUserNodes(user_uid)

    if (neo4j_result and result):
        print(f"successfully deleted the user with id {user_uid}")
        return True
    elif(neo4j_result):
        print(f"Error: received error while trying to delete neo4j nodes for user with id {user_uid}")
    elif(result):
        print(f"Error: received error while trying to delete mongo document for user with id {user_uid}")

def logout_user(user_uid):
    result = run_query({"user_uuid": user_uid}, "update", st.secrets["user_database"]["database_name"], st.secrets["user_database"]["collection_name"],update={"streamlit_logged_in":False})


# creates a new user in mongodb user database
def create_new_user(email, first_name,last_name, password, current_timestamp):
    input_password = password.encode('utf-8')
    user_uid = str(uuid.uuid4())[:18]

    print(f"user_uid: {user_uid}")

    # Generate a salt
    salt = bcrypt.gensalt()

    hashed_password = bcrypt.hashpw(input_password, salt)

    result = run_query({"email": email},"find","userDB","listings")

    # check to ensure there is no email already registered. 
    if result:
        print("Error: There is an Entry For This Email. Please Try Another")
        st.error("Error: There is an Entry For This Email. Please Try Another")
        return False
    else:
        document = {
            "name": f"{first_name} {last_name}",
            "user_uid": str(uuid.uuid4())[:18],
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "password_hash": hashed_password,
            "openai_api_token": "",
            "last_login": current_timestamp,
            "spotify_email": "",
            "pw_rst_token": "",
            "state": generate_state_parameter(),                 # used to track user when authorizing API. State is returned after API auth and is what ties back sessions
            "code_verifier":"",
            "spotify_display_name": "",
            "spotify_user_country": "",
            "num_followers": None,
            "streamlit_logged_in": True
        }

        result = run_query(document, "create", st.secrets["user_database"]["database_name"], st.secrets["user_database"]["collection_name"])

        print(f"user with email: {email} was successfully created")

        return True

# function updates an attribute with the value added to "attribute value" on the user document for the given email
def update_user_attribute(email, updates):
    result = run_query({"email":email}, "update", st.secrets["user_database"]["database_name"], st.secrets["user_database"]["collection_name"], update=updates)

    # Check if the update was successful
    if result.matched_count:
        print(f"updated successfully!")
    else:
        print("No matching document found.")

def store_spotify_user(results):
    updates = {
        "spotify_email": results["email"],
        "num_followers": results["followers"]["total"],
        "spotify_user_country": results["country"],
        "spotify_display_name":results["display_name"]
    }

    update_user_attribute(updates)

# pulls credentials of database based on email
def get_user_uid(email):
    result = run_query({"email": email}, "find", st.secrets["user_database"]["database_name"], st.secrets["user_database"]["collection_name"], projection={"user_uid":1, "_id":0})
    print(f"user_uid result: {result}")
    if not result:
        print(f"Error: {email} not in Mongo DB")
        return None
    else:
        return result["user_uid"]

# used to pull the state and code_verifier values when performing authentication
def get_auth_variables(state):
    results = run_query({"state": state}, "find", st.secrets["user_database"]["database_name"], st.secrets["user_database"]["collection_name"], projection={"user_uid":1, "code_verifier":1, "email":1, "name":1, "_id":0})

    if not results:
        print("Error: email does not exist")
        return False
    else:
        return results

############################################################################################################################

# makes a tooltip icon. Used for the stat metrics on spotify stats page
def makeTooltip(text_next_to_icon, tooltip_text):
    # HTML and CSS for the info icon and tooltip
    info_icon_html = f"""
                        <style>
                        .container {{
                            display: flex;
                            align-items: left;
                        }}
                        .tooltip {{
                            position: relative;
                            display: inline-block;
                            margin-left: 5px; /* Add some space between text and icon */
                        }}
                        .tooltip .tooltiptext {{
                            visibility: hidden;
                            width: 200px;
                            background-color: #191414;
                            color: #FFFFFF;
                            text-align: left;
                            border-radius: 6px;
                            padding: 5px;
                            position: absolute;
                            z-index: 1;
                            bottom: 125%;
                            left: 50%;
                            margin-left: -100px;
                            opacity: 0;
                            transition: opacity 0.3s;
                        }}
                        .tooltip .tooltiptext::after {{
                            content: "";
                            position: absolute;
                            top: 100%;
                            left: 50%;
                            margin-left: -5px;
                            border-width: 5px;
                            border-style: solid;
                            border-color: #191414 transparent transparent transparent;
                        }}
                        .tooltip:hover .tooltiptext {{
                            visibility: visible;
                            opacity: 1;
                        }}
                        .info-icon {{
                            color: #1DB954;
                            font-size: 1em;
                            cursor: pointer;
                        }}
                        .inline-text {{
                            display: inline-block;
                            font-size: 1.2em;
                        }}
                        </style>

                        <div class="container">
                            <div class="inline-text">{text_next_to_icon}</div>
                            <div class="tooltip">
                                <span class="info-icon">ℹ️</span>
                                <span class="tooltiptext">{tooltip_text}</span>
                            </div>
                        </div>
                        """

    st.markdown(info_icon_html, unsafe_allow_html=True)

###################################################### Report Queries #############################################################
# function pulls the number of tracks that exist in the database
@st.cache_data
def getNumberTracks(user_uid):
    query = f"""
        MATCH (t:Track)
        WHERE t.user_uid = "{user_uid}"
        RETURN count(t) AS track_count;
    """

    result = neo4jManager.getResultFromDB(query,params={},output_values=["track_count"])
    print(f"number of tracks array: {result}")
    return result["track_count"][0]

# function calculates the user's recency engagement score (RES). This score is a way of measuring
# the ratio of tracks that the user has listened to in total vs the tracks that a user has listened to in the past 90 days
@st.cache_data
def getRecencyEngagementScore(current_timestamp, user_uid):
    start_time = current_timestamp - 7776000
    
    query = f"""
        MATCH (n:Track)
        WHERE ANY(value IN n.play_history WHERE $start_time <= value <= $current_timestamp) AND n.user_uid = "{user_uid}"
        RETURN COUNT(n) AS totalCount
    """

    result = neo4jManager.getResultFromDB(query, params={"start_time":start_time,"current_timestamp":current_timestamp}, output_values=["totalCount"])
    totalTracks = getNumberTracks(user_uid)

    return round((result["totalCount"][0]/totalTracks)*100,0)

# function gets the total number of nodes that exist for a specific node_type
@st.cache_data
def getTotalNodes(node_type,user_uid):
    query = f"""
        MATCH (n:{node_type})
        WHERE n.user_uid = "{user_uid}"
        RETURN count(n) AS totalCount
        """
    

    params = {
        'node_type':node_type
    }

    result = neo4jManager.getResultFromDB(query, params, output_values=["totalCount"])
    return result["totalCount"][0]

@st.cache_data
def createNeo4jGraph(query):
    # Neo4j connection details
    neo4j_uri = f"bolt://{st.secrets["neo4j_database"]["host"]}:{st.secrets["neo4j_database"]["port"]}"
    neo4j_user = st.secrets["neo4j_database"]["username"]
    neo4j_password = st.secrets["neo4j_database"]["password"]

    # Render the graph in Streamlit
    st.components.v1.html(
        f"""
        <style type="text/css">
            html, body {{
                font: 16pt arial;
            }}

            #viz {{
                width: 100%;
                height: 700px;
                border: 5px black;
                font: 22pt arial;
                background-color: gray;
            }}
        </style>
        
        <script src="https://unpkg.com/neovis.js@2.0.2"></script>
        
        <script type="text/javascript">
            let viz;

            function draw() {{
                const config = {{
                    containerId: "viz",
                    neo4j: {{
                        serverUrl: "{neo4j_uri}",
                        serverUser: "{neo4j_user}",
                        serverPassword: "{neo4j_password}"
                    }},
                    labels: {{
                        Track: {{
                            label: "name",
                            color: {{
                                background: "#1DB954",
                                highlight: "#1DB954", 
                                border: "#FFFFFF"
                            }},
                            value: "popularity",
                            community: "track"
                        }},
                        Artist: {{
                            label: "name",
                            background: "#FF9800",
                            value: "popularity"
                        }}, 
                        Album: {{
                            label: "name",
                            background: "#0087C4",
                            value: "popularity",
                            community: "album"
                        }}, 
                        Playlist: {{
                            label: "name",
                            background: "#8001C2",
                            community: "playlist"
                        }},
                        Genre: {{
                            label: "name",
                            background: "#E91E63",
                            community: "genre"
                        }}    
                    }},
                    relationships: {{
                        MADE_BY: {{
                            static: {{
                                label: "Made By",
                                thickness: 10,
                                color: "#FFFFFF"
                            }}
                        }},
                        IN_ALBUM: {{
                            static: {{
                                label: "Part Of",
                                thickness: 10,
                                color: "#FFFFFF"
                            }}
                        }},
                        GENRE: {{
                            static: {{
                                label: "Genre",
                                thickness: 10,
                                color: "#FFFFFF"
                            }}
                        }},
                        IN_PLAYLIST: {{
                            static: {{
                                label: "Part Of",
                                thickness: 10,
                                color: "#FFFFFF"
                            }}
                        }}
                    }},
                    visConfig:{{
                        nodes:{{
                            shape:"dot",
                            size: 50,
                            font:{{
                                size : 9,
                                color:"#000000",
                                align: "center"
                            }},
                            color: {{ 
                                highlight: "#000000",
                                background: "#1DB954",
                                border: "#FFFFFF" 
                            }}
                        }},
                        edges:{{
                            arrows:{{
                                to:{{enabled: true}}
                            }},
                            smooth:{{type: 'continuous'}}
                        }}
                    }},
                    initialCypher: "{query}"
                }};

                viz = new NeoVis.default(config);
                viz.render();
            }}
        </script>

        <body onload="draw()">
            <div id="viz"></div>
        </body>

        """,
        height=800  # Adjust height as needed
    )


    # clear secret values
    neo4j_uri = ""
    neo4j_user = ""
    neo4j_password = ""


# function pulls the listens over a interval defined by 4 values (measure_type, start_day, end_day, and granularity). start_day should be a UTC timestamp in seconds. 
# Granularity determines how many datapoints will be returned. start_time dictates when values will start being produced. All values returned will start at start time, then move into the past based on measure_type and granularity.
# Note that this value will determine how many data points will be sent. Function returns a 2d pandas dataframe (time vs # plays)
# measure_type determines how the timeframes will be returned in the graphic. The options available are:
# 1. "Day": Graphic will show listens by day
# 2. "Hour": Graphic will show listens by the hour
# 3. "Week": Graphics Will show listens by the week
@st.cache_data
def getListensOverTime(user_uid, start_time,end_time,measure_type,granularity = 10,genre_name = None):
# create pandas dataframe
    data = {
        'Time': [],
        'Listens':[]
    }

    print("inputted start_time: {start_time}")

    time_difference = end_time - start_time
    delta = time_difference / granularity

    # check to make sure a valide measure type was returned
    if(measure_type != "Past Day" and measure_type != "Past Month" and measure_type != "Past 3 Months" and measure_type != "Custom" and measure_type != "Past 7 Days"):
        print("Error: measure_type does not have a valid entry. Please check your getListensOverTime function for spelling mistakes")
        return

    start_time = end_time - delta

    # Run query to pull a count of all the values in "play_history" that are between start_time and end_time for all tracks
    for i in range(granularity):

        # if genre name isn't specified. Search across all genres
        if genre_name is None:
            query = f"""
                MATCH (n:Track)
                WHERE n.user_uid = "{user_uid}"
                WITH n, [value IN n.play_history WHERE $start_time <= value <= $end_time] AS matchingValues
                RETURN sum(size(matchingValues)) AS totalCount
            """

            params = {
                'start_time': start_time,
                'end_time': end_time
            }
        else:
            # good example of how path approach of Graph DB is useful and quick
            query = """
                MATCH (n:Track)
                WHERE EXISTS {
                    MATCH (n)-[:IN_ALBUM]->(album)-[:GENRE]->(genre)
                    WHERE genre.name = $genre_name AND n.user_uid = "$user_uid"
                }
                WITH n, [value IN n.play_history WHERE $start_time <= value <= $end_time] AS matchingValues
                RETURN sum(size(matchingValues)) AS totalCount
            """

            params = {
                'start_time': start_time,
                'end_time': end_time,
                'genre_name': genre_name,
                'user_uid': user_uid
            }

        utc_datetime = datetime.fromtimestamp(end_time, tz=timezone.utc)
        local_datetime = utc_datetime.astimezone()

        if(measure_type == "Past Month" or measure_type == "Past 3 Months" or (measure_type == "Custom" and time_difference > 86400)):
            x = local_datetime.strftime('%b %d')

        elif(measure_type == "Past Day" or (measure_type == "Custom" and delta <= 86400)):
            x = f"{local_datetime:%A}, {local_datetime.hour % 12 or 12}{local_datetime:%p}"

        elif(measure_type == "Past 7 Days"):
            x = local_datetime.strftime('%A, %b %d')

        result = neo4jManager.getResultFromDB(query, params, ["totalCount"])         

        data['Time'].append(x)
        data['Listens'].append(result["totalCount"][0])

        # set previous start time to be the end time
        end_time = start_time
        start_time = end_time - delta
    
    # reverse the list order so that most recent day is at the end. Makes charts move in the correct time order
    data['Time'].reverse()
    data['Listens'].reverse()

    df = pd.DataFrame(data)
    #print(f"df: {df}")

    return df

# function pulls the most popular names of a certain node type (set with the variable node_type). 
# This can be set to Track, Playlist, Genre, or Artist. Use the variable "number_of_entries" to set how many names are returned (default is 5, max is 100)
# the function returns a list of names.
@st.cache_data
def getFavorites(user_uid, node_type, number_of_entries = 5):  

    # error check to ensure num_of_entries isn't greater than maximum     
    if number_of_entries > 100:
        print("Error: num of entries to pull is too high")
    else:
        data = {
            "name":[],
            "listens":[]
        }

         # Query returns the name of the i'th highest number of times played for selected node type
        query = f"""
            MATCH (n: {node_type})
            WHERE n.play_history IS NOT NULL AND n.user_uid = "{user_uid}"
            WITH n
            ORDER BY size(n.play_history) DESC
            RETURN n.name AS name,size(n.play_history) AS listens
            LIMIT {number_of_entries}
        """

        params = {"node_type":node_type, "num_of_entries":number_of_entries}
        result = neo4jManager.getResultFromDB(query,params,["name","listens"])

        for i in range(len(result["name"])):
            data["name"].append(result["name"][i])
            data["listens"].append(result["listens"][i])

        df = pd.DataFrame(data)    
        print(f"Dataframe: {df}")
        return df

# calculates the "obscurity score" for a user, which is a percentage that articulates how many unique artists, tracks, and albums they listen to. This is based off the popularity metric.
@st.cache_data
def calculateObscurityScore(user_uid):
    query = f"""
        MATCH(n:Track|Album)
        WHERE n.popularity IS NOT NULL AND n.user_uid = "{user_uid}"
        RETURN round(avg(n.popularity)) AS obscurityScore
    """
    result = neo4jManager.getResultFromDB(query, params={}, output_values=["obscurityScore"])
    return int(result["obscurityScore"][0])

@st.cache_data
def calculateDiversityScore(user_uid):
    # use a log function to flatten, and normalize with a constant to keep between 0 and 1
    return int((math.log((getTotalNodes(node_type = "Artist", user_uid=user_uid,) + 1),50)/1.6)*100)


# function gets the tracks that were played in a time period (a timestamp in seconds) defined by "loopback_time"
@st.cache_data
def getRecentlyPlayed(user_uid,node_type,current_time,lookback_time):
    start_time = current_time - lookback_time
    #print(f"start_time: {start_time}")
    #print(f"end_time: {current_time}")
    query = f"""
        MATCH (n:{node_type})
        WITH n, [value IN n.play_history WHERE value >= {start_time} AND value <= {current_time} AND n.user_uid = "{user_uid}"] AS matchingValues
        WHERE size(matchingValues) > 0  
        RETURN n.name AS recentlyPlayed
    """

    params = {
        'node_type': node_type,
        'start_time': start_time,
        'end_time': current_time
    }

    result = neo4jManager.getResultFromDB(query, params, output_values=["recentlyPlayed"])
    return result["recentlyPlayed"]

# function will create a pandas dataset that has 4 times of day (Morning, Afternoon, Evening, and Night), 
# each day will show the number of listens over a 90 day period for each period of the day
@st.cache_data
def getTimeOfDay(node_type, user_uid):
    data = {
        'Time': ["Morning","Afternoon","Evening","Night"],
        'Listens':[]
    }

    # dictionary defines the ranges for each time of day
    timesOfDay = {
        "Morning":(1,12),
        "Afternoon":(13,16),
        "Evening":(17,19),
        "Night":(20,24)
    }

    for i in timesOfDay.keys():
        query = f"""
            MATCH (n:{node_type})
            WITH n, [value IN n.hour_of_day WHERE {timesOfDay[i][0]} <= value <= {timesOfDay[i][1]} AND n.user_uid = "{user_uid}"] AS matchingValues
            RETURN sum(size(matchingValues)) AS totalCount
        """

        result = neo4jManager.getResultFromDB(query, params={},output_values=["totalCount"])

        data['Listens'].append(result["totalCount"][0])
    
    df = pd.DataFrame(data)

    return df

@st.cache_data
def getFavDetails(name, node_type, user_uid):

    print(f"name of {node_type} attempting to get favorites from: {name}")

    if(node_type == "Track"):
        query = f"""
        MATCH (n:{node_type} {{name: "{name}"}})-[:IN_ALBUM]->(a:Album)
        WHERE n.user_uid = "{user_uid}"
        RETURN a.image_url AS image_url, n.id AS id
        """
    else:
        query = f"""
        MATCH (n:{node_type} {{name: "{name}"}})
        WHERE n.user_uid = "{user_uid}"
        RETURN n.image_url AS image_url, n.id AS id
        """

    result = neo4jManager.getResultFromDB(query=query,params={}, output_values=["image_url","id"])

    print(f"image_url: {result["image_url"]}")
    return result["image_url"], result["id"]

# function creates a streamlit list of favorite 
@st.cache_data
def createFavoritesCol(user_uid, node_type, number_of_entries):

    st.markdown(f"<h4 style='text-align: left; color: white;'>Favorite {node_type}s</h4>", unsafe_allow_html=True)

    col4,col5, col6 = st.columns([1,3, 1])

    with col4:
        st.write("Name")
    
    with col6:
        st.write("Listens")

    results = getFavorites(user_uid=user_uid, node_type=node_type,number_of_entries=number_of_entries)
                
    for i in range(len(results["name"])):
        
        image_url, id = getFavDetails(user_uid=user_uid, name=results["name"][i],node_type=node_type)
        
        col4,col5, col6 = st.columns([1,3, 1])
        
        with col4:
            st.markdown("""
            <style>
            .favorite-images {
                width: 100% !important;
                height: 100% !important;
            }
            </style>
            """, unsafe_allow_html=True)

            st.markdown(f"""<img src="{image_url[0]}" class="favorite-images">""", unsafe_allow_html=True)
        
        with col5:
            match node_type:
                case "Track":
                    link = f"https://open.spotify.com/track/{id[0]}"

                case "Artist":
                    link = f"https://open.spotify.com/artist/{id[0]}"

                case "Playlist":
                    link = f"https://open.spotify.com/playlist/{id[0]}"
                
                case "Album":
                    link = f"https://open.spotify.com/album/{id[0]}"

            # Create clickable text using HTML
            st.markdown(
                f'<a href="{link}" target="_blank">{results["name"][i]}</a>',
                unsafe_allow_html=True
            )
        
        with col6:
            st.write(results["listens"][i])

    st.write("---")  # Separator between tracks

# function creates a pie chart based on a genre pandas df passed in. X axis is the name of the genre. Y axis is the number of listens
@st.cache_data
def makeGenrePieChart(favoriteGenres):
    # create donut chart
    fig, ax = plt.subplots()

    colors = ['#2eb82e', '#e6005c', '#e65c00', '#5500ff','#0000e6']
    ax.pie(favoriteGenres["listens"], labels=favoriteGenres["name"], colors = colors, autopct='%1.1f%%', startangle=90, wedgeprops=dict(width=0.45), pctdistance=0.85, labeldistance=1.05, textprops={'color': 'white'})

    ax.axis('equal')

    fig.patch.set_alpha(0)  
    ax.set_facecolor('none')


    # Display the chart
    st.pyplot(fig)


########################################################### Main ##################################################################
def main():
    st.set_page_config(layout="wide")

    #if the user_uid is stored in cache, set class variable in neo4jHelper
    if 'user_uid' in st.session_state:
        neo4jManager.user_uid = st.session_state['user_uid']
    
    # Load the CSS file
    cssFile = "style.css"
    with open(cssFile) as f:
        st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)


# ================================================== Login Code ==================================================================
    # Get the current UTC time
    current_utc_time = datetime.now(timezone.utc)

    # Convert to a UNIX timestamp
    utc_timestamp = int(current_utc_time.timestamp())
    print(f"utc_timestamp: {utc_timestamp}")

    print("Checking to see if logged in...")
    #print(f"session_state: {st.session_state['page_state']}"

    if 'page_state' in st.session_state:
        print(f"page_state: {st.session_state['page_state']}")
    else:
        print("page_state not in cache")

    
    # if code and state params are in url, use state to reapply user_uid to maintain session
    if ('code' and 'state' in st.query_params):
        print(f"state: {st.query_params['state']}")
        result = get_auth_variables(st.query_params['state'])
        print(f"result of pulling auth info: {result}")

        if(result != False):
            print(f"results: {result}")

            # set cache params from info from DB
            st.session_state['user_email'] = result['email']
            st.session_state['user_uid'] = result['user_uid']
            st.session_state['user_name'] = result['name']

            neo4jManager.user_uid = result['user_uid']

            # set page_state to be 0 to allow for Spotify to pull the refresh token in the 0th page state
            st.session_state['page_state'] = 0
            st.session_state['streamlitLoggedIn'] = True

            print(f"name: {st.session_state['user_name']}")
            print(f"user_uid: {st.session_state['user_uid']}")
            print(f"user_email: {st.session_state['user_email']}")
        
        # state mismatch if a state cant be found in db
        else:
            print("Error: State could not be found")
            print("Error: State mismatch in API Call. Could Be a Potential CSRF Attack")
            st.session_state['page_state'] = 2
            st.session_state['error_message'] = "Error: State mismatch in API Call. Could Be a Potential CSRF Attack" 

    # if params not in there, then check cache for all needed variables and adjust the state of page depending on if they exist and a refresh token is in db
    else:
        # ensure streamlit login cache state variable exists. If not, set to false and rerun
        if 'streamlitLoggedIn' not in st.session_state:
            print("streamlitLoggedIn not added to cache.. adding...")
            st.session_state['streamlitLoggedIn'] = False
            st.session_state['page_state'] = 2
            st.rerun()
        
        #if streamlit login state is false and user isn't signing up, set page_state
        elif st.session_state['streamlitLoggedIn'] == False and st.session_state['page_state'] != 3:
            st.session_state['page_state'] = 2

        # if streamlit logged in, check that proper cache variables are added, then check if there is a valid refresh token for the user
        elif st.session_state['streamlitLoggedIn'] == True:

            # if not authorizing, then check that 'email' is stored in cache. If it is, use it 
            if ('email' and 'user_uid' not in st.session_state):
                st.session_state['page_state'] = 2
                st.rerun()
                    
            # if email is in cache, but not uid, attempt to pull from db, and store into cache
            elif 'email' in st.session_state and 'user_uid' not in st.session_state:
                result = run_query({'email':st.session_state['email']}, "find", st.secrets['user_database']['database_name'], st.secrets['user_database']['collection_name'],projection={"user_uid":1, "name":1, "_id":0})
                st.session_state['user_uid'] = result['user_uid']
                st.session_state['user_name'] = result['name']

                if not result:
                    print(f"Error: {st.session_state['email']} does not have a user uid... returning to login...")
                    st.session_state['page_state'] = 2
                else:
                    st.session_state['page_state'] = 1
            elif 'user_uid' in st.session_state and 'email' not in st.session_state:
                result = run_query({'user_uid':st.session_state['user_uid']}, "find", st.secrets['user_database']['database_name'], st.secrets['user_database']['collection_name'],projection={"email":1, "name":1, "_id":0})
                st.session_state['email'] = result['email']
                st.session_state['user_name'] = result['name']

                if not result:
                    print(f"Error: {st.session_state['user_uid']} does not have an email... returning to login...")
                    st.session_state['page_state'] = 2
                else:
                    st.session_state['page_state'] = 1
            
            # if user_uid and email are in cache, check to see if refresh token is in DB, if so, generate dashboard
            if ('email' and 'user_uid' in st.session_state):
                if neo4jManager.checkRefreshToken(apiManager,utc_timestamp):
                    st.session_state['page_state'] = 1
                else:
                    st.session_state['page_state'] = 0

    

    # if refresh token doesn't exist in db or is expired, create login button that will initiate oauth flow
    if(st.session_state['page_state'] == 0):
        print("Within Login Session")

        # if button is pressed, generate auth url, store state and verifier in DB, and redirect user to spotify auth page
        # need to store in DB and not using caching is because when redirected back, a new streamlink session is started, and cache 
        # is reset..
        with st.container():
            # Create a layout with three columns
            col1, col2, col3 = st.columns([1, 1, 1])
            
            with col1:
                if st.button("Login To Spotify"):
                    
                    
                    print(st.session_state['user_email'])
                    # Generate auth url to be used
                    result = run_query({"email":st.session_state['user_email']}, "find", st.secrets["user_database"]["database_name"], st.secrets["user_database"]["collection_name"], projection={'state':1, '_id':0})
                    auth_url,code_verifier = apiHelper.getAuthCodeURL(result['state'])
                
                    params = {
                        'state':result['state'],
                        'code_verifier':code_verifier
                    }

                    print(f"params for auth (state): {result}")

                    # stores state and code_verifier in user db before navigating user to spotify API auth page
                    result = run_query({"user_uid":st.session_state['user_uid']},"update", st.secrets["user_database"]["database_name"], st.secrets["user_database"]["collection_name"], update={"state": result['state'], "code_verifier":code_verifier})

                    # create a new session, using the state generated as a "session_id" will be used to track sessions because streamlit resets cache
                    # when a redirect occurs
                    print("generated Auth URL..")
                    print("Login Button Pressed.. Running Oauth Flow..")
                    nav_to_auth_url(auth_url)

                    # Wait for user to authorize scope by checking if url has auth code param inside it
                    i = False
                    while 'code' not in st.query_params:
                        if(i == False):
                            i=True
                            print("Waiting For User To Auth")
                        #print(f"query params before webpage is opened: {st.query_params}")
        
        # If we are being redirected back from Authorization page, then pull the auth code, use it to get a refresh token, and store in DB        
        if 'code' and 'state' in st.query_params:
            print("in code/state")
            st.empty()
            with st.spinner("Pulling Spotify Stats..."):
                neo4jManager.user_uid = st.session_state['user_uid']

                # get a new refresh token and access token, and store in DB
                print("getting access token to sync\n")
                access_token, refresh_token = apiManager.getRefreshToken(Neo4jManager=neo4jManager,auth_code=st.query_params['code'],code_verifier=result['code_verifier'])

                print(f"access_token: {access_token}")
                print(f"refresh_token: {refresh_token}")

                #if access token isn't able to get pulled, throw an error and store metric in DB to tell front end that refresh token is expired
                if access_token is None:
                    st.error('Error: access token was unable to be pulled.. this likely means the refresh token is expired. Rerun so user can reauthorize', icon=":material/sentiment_dissatisfied:")
                    neo4jManager.storeRefreshTokenExpired(True)
                    st.session_state['page_state'] = 0
                    st.rerun()
                else:
                    print("Access Token successfully pulled")
                    neo4jManager.storeRefreshTokenExpired(False)
                    neo4jManager.storeRefreshToken(refresh_token)
                
                # set state to display dashboard, clear the params returned by API, then make some balloons pop!
                st.session_state['page_state'] = 1
                st.session_state['streamlitLoggedIn'] = True
                st.query_params.clear()
                st.balloons()
                print(f"page_state: {st.session_state['page_state']}")

                st.rerun()
        else:
            st.title("Spotify Stats Page")
            st.write(
                f"""This application showcases statistics about your Spotify listens. Simply login to your Spotify account by selecting 'Login to Spotify' on the top right, approve the API scope, and the stats will automatically be generated.
                """
                )
            if st.button("Back To Home"):
                logout_user(st.session_state['user_uid'])
                st.session_state['streamlitLoggedIn'] = False 
                st.rerun()
  
# =================================================================================== Main Dashboard ==============================================================================                 
    elif(st.session_state['page_state'] == 1):
        print("Enabling Dashboard View")

        progress_text = "Pulling Your Recently Listened to Songs..."
        my_bar = st.progress(0, text=progress_text)  # Initialize progress bar at 0%

        expired = API2DB(user_uid = neo4jManager.user_uid, utc_timestamp=utc_timestamp,my_bar=my_bar)
        my_bar.empty()

        with st.container():
            # Create a layout with three columns
            col1, col2, col3 = st.columns([1, 5, 1])

            with col1:
                st.image("Images/logo.png", use_container_width=True)
            
            with col3:

                # if spotify_email is not pulled, then pull the info with the API, store it in cache, and put extra info in MongoDB
                #if 'spotify_email' not in st.session_state:
                    #results = apiManager.getAPIResponse(type="user")
                    #st.session_state['spotify_email'] = results["email"]
                    #store_spotify_user(results)

                st.write(f"Logged In As {st.session_state['email']}")

                #if user selects to logout, remove refresh token from DB
                if(st.button("Logout")):
                    logout_user(st.session_state['user_uid'])
                    st.session_state['streamlitLoggedIn'] = False 
                    st.rerun()

        st.markdown("<h1 style='text-align: left; font-size:75px; color: white;'>Spotify Stats</h1>", unsafe_allow_html=True)    

        #get uid of user if not pulled, then pull any recent songs from Spotify and put into DB 
        if "user_uid" not in st.session_state:
            result = get_user_uid(st.session_state["user_email"])
            print(f"result of user_uid: {result}")
            if result is None:
                st.error(f"Error: {st.session_state['email']} is not a valid email. Please sign up or try retyping email")
                st.session_state['page_state'] = 2
                st.rerun()
            else:
                st.session_state['user_uid'] = result
  
        my_bar = st.progress(0, text = "Loading Tracks... Please Wait")
        expired = API2DB(utc_timestamp = utc_timestamp, user_uid = st.session_state["user_uid"], my_bar=my_bar)
        my_bar.empty()

        with st.container():
            col1,col2,col3=st.columns([20,1,20])

            with col1:
                st.markdown(f"<h2 style='text-align: left; color: white;'>Genre Breakout</h2>", unsafe_allow_html=True)
                makeGenrePieChart(getFavorites(user_uid=st.session_state["user_uid"], node_type="Genre", number_of_entries=10))
            
            with col3:
                st.markdown(f"<h2 style='text-align: left; color: white;'>Metrics</h2>", unsafe_allow_html=True)

                makeTooltip(text_next_to_icon=f"Obscurity Score: {calculateObscurityScore(user_uid=st.session_state["user_uid"])}%",tooltip_text="Your Obscurity Score Ranges From 0 to 100, and Quantifies How Obscure The Music You Listen to is")

                makeTooltip(text_next_to_icon=f"Diversity Score: {calculateDiversityScore(user_uid=st.session_state["user_uid"],)}%",tooltip_text="Your Diversity Score Ranges From 0 to 100, and Quantifies How Much Variety of Music You Listen to")

                makeTooltip(text_next_to_icon=f"Recency Engagement Score: {getRecencyEngagementScore(current_timestamp=utc_timestamp, user_uid=st.session_state["user_uid"])}%",tooltip_text="Recency Engagement Score Ranges From 0 to 100, and Quantifies the Ratio of Tracks You've Listened to Recently vs. All The Tracks You've Listened")

                makeTooltip(text_next_to_icon=f"Total Tracks Listened To: {getTotalNodes(user_uid=st.session_state["user_uid"], node_type="Track")}",tooltip_text="Total Tracks You've Listened To")

                makeTooltip(text_next_to_icon=f"Total Albums Listened To: {getTotalNodes(user_uid=st.session_state["user_uid"], node_type="Album")}",tooltip_text="Total Albums You've Listened To")

                makeTooltip(text_next_to_icon=f"Total Genres Listened To: {getTotalNodes(user_uid=st.session_state["user_uid"], node_type="Genre")}",tooltip_text="Total Genres You've Listened To")


        #if refresh token is expired, then rerun page
        if expired == "refreshTokenExpired":
            st.rerun()

        with st.container():
            col1,col2,col3=st.columns([15,1,15])

            with col1:
                # add title
                st.markdown(f"<h2 style='text-align: left; color: white;'>Listens Based on Time of Day</h2>", unsafe_allow_html=True)

                df = getTimeOfDay(user_uid=st.session_state["user_uid"], node_type="Track")

                # Create the Altair chart
                chart = alt.Chart(df).mark_bar(color='green').encode(
                    x=alt.X('Time', sort=None, title='Time'),
                    y=alt.Y('Listens', title='Listens')
                ).properties(
                    title=''
                )

                # Display the chart in Streamlit
                st.altair_chart(chart, use_container_width=True)

            with col3:
                # add title
                st.markdown(f"<h2 style='text-align: left; color: white;'>Listens Over Time</h2>", unsafe_allow_html=True)
                
                measure_type = st.selectbox('Time Length', ['Past Day','Past 7 Days','Past Month','Past 3 Months','Custom'], key='listens_over_time_selector')
                end_timestamp = utc_timestamp

                match measure_type:
                    case 'Past Day':
                        # set start time to be 2 weeks before and make granularity 14 days
                        granularity = 24
                        x_label = 'Hour' 
                        start_timestamp = utc_timestamp - 86400

                    case 'Past 7 Days':
                        # set start time to be 1 day before and make granularity 24
                        granularity = 7
                        x_label = 'Day'
                        start_timestamp = utc_timestamp - 604800
                    
                    case 'Past Month':
                        # set start time to be 1 day before and make granularity 24
                        granularity = 30
                        x_label = 'Day'
                        start_timestamp = utc_timestamp - 18144000
                    
                    case 'Past 3 Months':
                        # set start time to be 1 day before and make granularity 24
                        granularity = 30
                        x_label = 'Week'
                        start_timestamp = utc_timestamp - 55036800
                    
                    case 'Custom':
                        granularity = 30
                        x_label = 'Day'
                        with st.container():
                            col1_embeded, col2_embeded = st.columns([1,1])

                            today = date.today()

                            with col1_embeded:
                                seven_days_ago = today - timedelta(days=7)

                                start_date = st.date_input(label = "Start Date", value = seven_days_ago)
                                start_timestamp = convert_to_utc_timestamp(start_date)

                            
                            with col2_embeded:
                                end_date = st.date_input(label = "End Date", value = today, max_value = "today") 
                                end_timestamp = convert_to_utc_timestamp(end_date)

                result = getListensOverTime(user_uid=st.session_state["user_uid"], start_time=start_timestamp, end_time=end_timestamp, measure_type=measure_type,granularity=granularity)

                # Create an Altair chart with the specified features
                chart = alt.Chart(result).mark_line(color='green').encode(
                    x=alt.X('Time', sort=None, title='Time'),  # 'sort=None' preserves the original order
                    y=alt.Y('Listens', title='Listens')
                ).properties(
                    width=600,
                    height=400
                ).configure_axis(
                    grid=True
                ).configure_view(
                    strokeWidth=0
                )

                # Display the chart using Streamlit
                st.altair_chart(chart, use_container_width=True)

        st.write(f"\n\n\n\n")

        # note: need to figure out why you cant change dropdown when you have 2 tables next to each other
        #st.markdown("
        #<style>
        #.stSelectbox > div {
        #    width: 67px !important;  # Adjust width as needed
        #}
        #</style>
        #, unsafe_allow_html=True)
    
        #number_of_entries = st.selectbox(
        #    "Entries",
        #    options=[5, 10, 15, 20],
        #    index=0,
        #    key=f"entries_selector{random.randint(1,2000000)}"
        #)    

        with st.container():
            col1,col2,col3,col4 = st.columns([1,1,1,1])

            with col1:  
                createFavoritesCol(user_uid=st.session_state["user_uid"], node_type="Track", number_of_entries=5)

            with col2:  
                createFavoritesCol(user_uid=st.session_state["user_uid"], node_type="Artist", number_of_entries=5)   

            with col3:  
                createFavoritesCol(user_uid=st.session_state["user_uid"], node_type="Album", number_of_entries=5)

            with col4:  
                createFavoritesCol(user_uid=st.session_state["user_uid"], node_type="Playlist", number_of_entries=5) 

        st.markdown("<h2 style='text-align: left; font-size:40px; color: white;'>Neo4j Database</h2>", unsafe_allow_html=True) 
        query = st.text_input("Database Query", value="MATCH (n)-[r]->(m) RETURN n, r, m")
        createNeo4jGraph(query)

    # if page state is 2, display a login page to login to spotify app
    elif st.session_state['page_state'] == 2:
        
        with st.container():
            # Create a layout with three columns
            col1, col2 = st.columns([1, 1])
            
            with col1:
                st.title("Spotify Stats App")
            
            with col2:
                if st.button("Create An Account"):
                    st.session_state['page_state'] = 3
                    st.rerun()
        
        if 'success_message' in st.session_state:
            if st.session_state['success_message'] != "":
                st.success(st.session_state['success_message'])

        st.markdown("<h1 style='text-align: left; font-size:30px; color: white;'>Login</h1>", unsafe_allow_html=True)  

        with st.container():
            col1, col2 = st.columns([1,2])

            with col1:
                email = st.text_input(label="Email", key="Email_Login")
                password = st.text_input(label="Password",type="password",key="Password_login")

                if st.button("Login"):
                    result = validate_password(email,password)
                    
                    if(result == "loggedIn"):
                        password = ""
                        st.session_state['streamlitLoggedIn'] = True
                        st.session_state['user_email'] = email

                        st.session_state['user_uid'] = get_user_uid(st.session_state['user_email'])

                        result = run_query({"user_uuid": st.session_state['user_uid']}, "update", st.secrets["user_database"]["database_name"], st.secrets["user_database"]["collection_name"],update={"last_login":current_utc_time})


                        # calculate state variable used in API auth
                        state = secrets.token_urlsafe(36)

                        email = ""
                        password = ""
                        st.rerun()

                    elif (result == "passwordMismatch"):
                        st.session_state['error_message'] = f"Error: Incorrect Credentials. Please try again.."

                    elif (result == "noUser"):
                        st.session_state['error_message'] = f"Error: account with this email does not exist. Please create an account by selecting 'Sign Up'"

                if 'error_message' in st.session_state:
                    st.error(st.session_state.error_message)
                    del st.session_state["error_message"]
            
                    
    # if page state is 3, show sign up form
    elif st.session_state['page_state'] == 3:
        with st.container():
            col1, col2, col3, col4 = st.columns([1, 2, 2, 1])
            
            with col1:
                if st.button("Login"):
                    st.session_state['page_state'] = 2
                    st.rerun()

        st.markdown("<h1 style='text-align: left; font-size:50px; color: white;'>Spotify Stats Sign Up</h1>", unsafe_allow_html=True)  

        with st.container():
            col1, col2 = st.columns([1,2])

            with col1:
                email = st.text_input(label="Email", key="email")
                first_name = st.text_input(label="First Name", key="first_name")
                last_name = st.text_input(label="Last Name", key="last_name")
                password = st.text_input(label="Password",type="password",key="password")

                if 'error_message' in st.session_state:
                    st.error(st.session_state.error_message)
                    del st.session_state["error_message"]

                if st.button("Sign Up"):
                    result = create_new_user(email=email, first_name=first_name, last_name=last_name, password=password, current_timestamp=utc_timestamp)
                    st.session_state["user_email"] = email

                    if result:
                        st.session_state['success_message'] = "Login Successfully Created"
                        st.session_state["user_email"] = email
                        st.session_state['page_state'] = 2
                    else:
                        st.session_state["error_message"] = "Error: User Already Exists. Please Enter a Different Email"
                        st.session_state['success_message'] = ""
                        st.session_state['page_state'] = 3
                    
                    password = ""
                    email = ""
                    first_name = ""
                    last_name = ""
                    password = ""

                    st.rerun()

    else:
        st.write(st.session_state["error_message"])
    
#============================================================================================================================================================
if __name__ == "__main__":
    main()
