import requests
import pkce
from urllib.parse import urlencode,quote_plus
from http.server import HTTPServer
import json
from datetime import datetime, timezone
import neo4j
from neo4j import GraphDatabase, exceptions
import streamlit as st
import logging
from pymongo import MongoClient
from neo4j.exceptions import Neo4jError
import smtplib
import traceback
from email.mime.text import MIMEText

################################### Code To Pull Auth Code/Token ##################################
# This Portion of the Code Does The Following:
# 1. Generates a PKCE challenge and state token to prevent security attacks
# 2. Creates an Authorization URL based on the instructions here: https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow
# 3. The Auth URL is populated into a web browser where the user is prompted to login and authorize the scope needed by the python app
# 4. The browser then sends a response to a redirect url with an Auth Code (used to generate refresh tokens) and a State Code (used for additional security)
# 5. We send a request to pull a refresh token with our Auth Code, and store the token to make additional API calls

# What is PKCE and How Does It Enhance Security?
#      PKCE removes the need of a static Secret that needs to be stored in this code, it makes it harder for an attacker to
#      intercept the Auth Code, and ensures that the client requesting the token is the same client that initiated the request. 
#      Instead of using a secret we instead create a high entropy random string (code verifier), then hash it to create a PKCE Code Challenge.
#      This Challenge is then sent to the Auth Server every time we grab a refresh token or access code
#


####################################    Global Variable    ######################################
AUTHORIZATION_URL = 'https://accounts.spotify.com/authorize'                            # URL Used to Pull Authorization Code
TOKEN_URL = 'https://accounts.spotify.com/api/token'                                    # Token endpoint URL
CLIENT_ID = '951eba0a5d2e4d3b800d74f24b0cd84c'                                          # Your OAuth2 client ID
REDIRECT_URI = 'http://73.168.44.86:8501'                                               # Redirect URI 
SCOPE = 'playlist-read-private playlist-read-collaborative user-read-recently-played user-read-private user-read-email'   # Scopes requested from the API
PORT = 8000

# endpoints
GET_RECENTLY_PLAYED_URL = 'https://api.spotify.com/v1/me/player/recently-played'

# variables that dictate where error emails are sent from and sent to
sender = "spotify-app@mpalsec.com" 
receiver = "mpalmail@protonmail.com"

# Logging Variables
LOGGING_FILEPATH = 'Logs/Spotify2DBPythonScriptLogs.log'

####################################    Classes    ######################################

#used for making API Commands
class apiHelper():
    def __init__(self, url = None, headers = None, params = None):
        self.endpoint = url
        self.headers = headers
        self.params = params

    def getAuthCodeURL(state):
        code_verifier = pkce.generate_code_verifier(length=128)
        code_challenge = pkce.get_code_challenge(code_verifier)
        
        # Build the authorization URL with PKCE and state variable
        params = {
            'response_type': 'code',
            'client_id': st.secrets['spotify_api']['client_id'],
            'state': state,
            'scope': SCOPE,
            'code_challenge_method': 'S256',
            'code_challenge': code_challenge,
            'redirect_uri': REDIRECT_URI
        }

        # Create the full Auth URL (With Required Parameters)
        auth_url = f"{AUTHORIZATION_URL}?{urlencode(params)}"
        return auth_url,code_verifier

    def getRefreshToken(self, Neo4jManager, auth_code = None,code_verifier = None,refresh_token = None):
        #Exchange the authorization code for an access token if an auth code is provided
        if refresh_token is not None:
            token_data = {
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
                'client_id': st.secrets['spotify_api']['client_id']
            }
        #use the refresh token to fetch a new access token if provided
        elif auth_code is not None:
            token_data = {
                'grant_type': 'authorization_code',
                'code': auth_code,
                'redirect_uri': REDIRECT_URI,
                'client_id': st.secrets['spotify_api']['client_id'],
                'code_verifier': code_verifier  # Important: Use the original code verifier
            }
        else:
            print("Error: No Auth Code or Refresh Token Was Provided. Please Attach one of these to function")
            return None,None

        response = requests.post(TOKEN_URL, data=token_data)

        if response.status_code == 200:
            tokens = response.json()
            access_token = tokens['access_token']
            refresh_token = tokens.get('refresh_token')

            if Neo4jManager.storeRefreshToken(refresh_token):
                print("refresh token successfully stored")
            else:
                print("Error: refresh token could not be stored")

            return access_token,refresh_token
        
        else:
            print(f"Failed to get tokens: {response.status_code} {response.text}")
            return None,None

    ''' function that handles reading API Calls. The type variable determines which API Call we are making. 
        
        There are types of calls (set the variable to this to make the call):
            1. "artist" - Gets info about a specific artist
            2. "album" - Gets info about a specific album
            3. "playlist" - Gets info about a specific user's playlist
            4. "recently_played" - Gets all tracks played before a specific timestamp
            5. "user" - gets information about the currently logged in user

        In cases 1-3, you will need to add either the spotify ID of that artist/playlist/album or must include a 
        full url (for pagination purposes). For the recently played option, an after variable must be included. This is 
        a UTC timestamp, and any song played after will be returned by the response.
    '''
    def getAPIResponse(self,type,id=None,url=None):
        #pull all responses until "next" field is null. "next" field in response contains URL for next page of results
        next_url = "default"
        results = None

        match type:
            case "artist":
                if url is not None:
                    self.endpoint = url
                else:
                    self.endpoint = "https://api.spotify.com/v1/artists/" + id
                self.params = None
                
            case "album":
                if url is not None:
                    self.endpoint = url
                else:
                    self.endpoint = "https://api.spotify.com/v1/albums/" + id
                self.params = None
            
            case "playlist":
                if url is not None:
                    self.endpoint = url
                else:
                    self.endpoint = "https://api.spotify.com/v1/playlists/" + id
                self.params = None

            case "recently_played":
                self.endpoint = "https://api.spotify.com/v1/me/player/recently-played"

                self.params = {
                    "limit": "50"
                }
            
            case "user":
                if url is not None:
                    self.endpoint = url
                else:
                    self.endpoint = "https://api.spotify.com/v1/me"
                self.params = None
                
        # make API call, get response, and store in object if there are no errors
        api_response = requests.get(self.endpoint, params=self.params, headers=self.headers)
        if api_response.status_code == 200:
            
            # store response in dictionary
            api_response_json = api_response.json()

            # store results
            if type == "recently_played":
                results = api_response_json.get("items")
            else:
                results = api_response_json

        else:
            print(f"Failed to fetch API data: {api_response.status_code} {api_response.text}")
            logging.error(f"Failed to fetch API data: {api_response.status_code} {api_response.text}")

            # Return a 0 for error handling in function
            return 0
        
        return results


        
#a helper class used to create and read nodes in Neo4j. It is used to create and match nodes, along with creating paths between nodes
class Neo4jHelper:
    def __init__(self, user_uid, params = None):
        self.params = params
        self.user_uid = user_uid

    # General query to make db calls to neo4j. Pass along query and query params. Error Handling incorporated into this as well.
    def runQuery(self, query, params = None):
        try:
            db_uri = f"bolt://localhost:{st.secrets['neo4j_database']['port']}"
            with GraphDatabase.driver(db_uri, auth=(st.secrets['neo4j_database']['username'],st.secrets['neo4j_database']['password'])) as driver:
                driver.verify_connectivity()

                try:
                    result = driver.execute_query(query, params, result_transformer_= neo4j.Result.to_df)

                    #logging.info(f"Neo4j Query Successful. Results returned were: {result}\n\nQuery ran was: {query}")
                    return result

                except Neo4jError as e:
                    print(f"Neo4j error occurred: {e.code} - {e.message}")
                    return None

                finally:
                    # Close the driver session
                    driver.session().close()

                return result

        except exceptions.ServiceUnavailable as e:
            # Handle connection error, possibly retry
            print(f"Service unavailable error: {str(e)}")

        except exceptions.AuthError as e:
            # Handle incorrect credentials
            print(f"Authentication error: {str(e)}")

        except exceptions.CypherSyntaxError as e:
            # Handle query syntax error
            print(f"Cypher syntax error: {str(e)}")

        except exceptions.ConstraintError as e:
            # Handle unique constraint violations, etc.
            print(f"Constraint violation error: {str(e)}")

        except exceptions.TransactionError as e:
            # Handle transaction issues
            print(f"Transaction error: {str(e)}")

        except Exception as e:
            # Catch any other errors that are not covered above
            print(f"An unexpected error occurred: {str(e)}")

        finally:

            pass

    # deletes all nodes associated with a specific user uid
    def deleteUserNodes(self, user_uid):
        # f all nodes in DB associated with user
        query = f"""
            MATCH(n) WHERE n.user_uid = '{user_uid}' DETACH DELETE n RETURN count(n) AS deleted_count
        """

        neo4j_result = Neo4jHelper.runQuery(self,query,{'user_uid':user_uid})
        print(f"Results From deleteUserNodes: {neo4j_result}")

        if neo4j_result is not None:

            if neo4j_result['deleted_count'][0] > 0:
                print("User successfully deleted")
                return neo4j_result['deleted_count'][0]
            else:
                print("Error: user was not successfully deleted")
                return False
        else:
            print("neo4j query failed")
            return False


    # helper function to get results from DB. output_values is used to tell function what variables to pull. Output is a dictionary of lists containing
    # DB values. The keys in dictionary correspond to the output_value inputs
    def getResultFromDB(Neo4jManager,query,params,output_values=[]):
        results = {}
        result = Neo4jManager.runQuery(query=query, params=params)

        if result is not None:
            for i in range(len(output_values)):
                results[output_values[i]] = []

            for i in range(len(output_values)):
                for j in range(len(result)):
                    results[output_values[i]].append(result.iloc[j][output_values[i]])

            return results

        else:
            print("Query failed but program is continuing.")       
            return False


    # pulls Boolean indicator that indicates whether a refresh token is expired or not
    def getRefreshTokenExpired(self):
        query = f"""
            MATCH (n:Config)
            WHERE n.user_uid = "{self.user_uid}"
            RETURN n.refresh_token_expired AS refreshTokenExpired
        """
        
        result = self.getResultFromDB(query,params = {},output_values=['refreshTokenExpired'])

        if result is not None:
            print("Query Succeeded:", result)
            return result['refreshTokenExpired'][0]

        else:
            print("neo4j query failed")
            return False

    # pulls Boolean indicator that indicates whether a refresh token is expired or not
    def storeRefreshTokenExpired(self,value):
        
        query = f"""
            MATCH (n:Config)
            WHERE n.user_uid = "{self.user_uid}"
            SET n.refresh_token_expired = $value
            RETURN n.refresh_token_expired AS output
        """

        result = self.getResultFromDB(query,params = {"value":value},output_values=['output'])
        print(f"Results From storeRefreshTokenExpired: {result}")
        
        if result is not None:
            return True

        else:
            print("neo4j query failed while sto")
            return False

    # Checks if a node in DB Exists
    def check_node_exists(self,id="",type=""):
        match type:
            case "track":
                query = f"""
                MATCH (n:Track {{id: "{id}"}}) 
                WHERE n.user_uid = "{self.user_uid}"
                RETURN COUNT(n) > 0 AS exists
                """

            case "album":
                query = f"""
                MATCH (n:Album {{id: "{id}"}}) 
                WHERE n.user_uid = "{self.user_uid}"
                RETURN COUNT(n) > 0 AS exists
            """
            
            case "artist":
                query = f"""
                MATCH (n:Artist {{id: "{id}"}}) 
                WHERE n.user_uid = "{self.user_uid}"
                RETURN COUNT(n) > 0 AS exists
                """

            case "playlist":
                query = f"""
                MATCH (n:Playlist {{id: "{id}"}}) 
                WHERE n.user_uid = "{self.user_uid}"
                RETURN COUNT(n) > 0 AS exists
                """
            
            case "genre":
                query = f"""
                MATCH (n:Genre {{id: "{id}"}}) 
                WHERE n.user_uid = "{self.user_uid}"
                RETURN COUNT(n) > 0 AS exists
                """
            
            case "config":
                query = f"""
                MATCH (n:Config {{name: "configuration"}}) 
                WHERE n.user_uid = "{self.user_uid}"
                RETURN COUNT(n) > 0 AS exists
                """
            
            case _:
                print(f"Error: unrecognized type: {type}")
                return
        result = self.getResultFromDB(query=query,params={"id":id},output_values=['exists'])

        return result['exists'][0]
        
    # function is used to determine whether we have a valid refresh token. Function will also reauthorize the API if the variable authAPI is set to True (done by default)
    # if the refresh token exists in the DB, then we will assume it is valid. Error handling in App.py will deal with exception of an expired refresh token
    def checkRefreshToken(self,apiManager,utc_timestamp):

         # check if config node (used to store metadata/tokens) exists in DB. If not, create it
        if not self.check_node_exists(type="config"):

            # set initial timestamp to be 6 months in the past to ensure we are pulling as many songs as possible
            params = {
                "user_uid": self.user_uid,
                "last_sync_timestamp": (utc_timestamp - 15811200),
                "refresh_token":"", 
                "refresh_token_expired":True
            }

            self.createNode(type="config",params=params)

        # grab refresh token from DB
        refresh_token = self.getRefreshTokenFromDB()

        if refresh_token == "":

            # try pulling a new access token/refresh token
            print("Fetching New Refresh Token")
            access_token, refresh_token = apiManager.getRefreshToken(Neo4jManager=self, refresh_token = refresh_token)

            if (refresh_token is None):
                print("refresh token is expired... ")
                logging.info('refresh token is expired... ')

                return False
            else:
                print("Storing Refresh Token in DB")
                self.storeRefreshToken(refresh_token = refresh_token)
                return True
        else:
            print("Refresh Token Exists in DB")
            access_token, refresh_token = apiManager.getRefreshToken(Neo4jManager=self, refresh_token = refresh_token)
            return True

    # stores API refresh token into the database
    def storeRefreshToken(self,refresh_token):

        query = f"""
                MATCH (n:Config{{name:"configuration"}})
                WHERE n.user_uid = "{self.user_uid}"
                SET n.refresh_token = "{refresh_token}"
                """
        
        result = self.runQuery(query)

        if result is not None:
            return True
        else:
            print("Query failed but program is continuing.")       
            return False
    
    # gets API refresh token from database
    def getRefreshTokenFromDB(self):
        query = f"""
        MATCH (n:Config) 
        WHERE n.user_uid = "{self.user_uid}"
        RETURN n.refresh_token AS refresh_token
        """

        result = self.getResultFromDB(query=query,params={},output_values=['refresh_token'])
        if not result['refresh_token']:
            return ""
        else:
            return result['refresh_token'][0]
    
    # gets the Last Sync Timestamp from the Database
    def getTimestamp(self):
        query = f"""
        MATCH (n:Config) 
        WHERE n.user_uid = "{self.user_uid}"
        RETURN n.last_sync_timestamp AS last_sync_timestamp
        """

        timestamp = self.getResultFromDB(query=query,params={},output_values=['last_sync_timestamp'])
        
        print(f"Timestamp from DB: {timestamp['last_sync_timestamp'][0]}")
        logging.info(f"Timestamp from DB: {timestamp['last_sync_timestamp'][0]}")
        return timestamp['last_sync_timestamp'][0]
    
    # stores Last Sync Timestamp in Database
    def storeTimestamp(self,timestamp):
        query = f"""
                MATCH (n:Config{{name: 'configuration'}})
                WHERE n.user_uid = "{self.user_uid}"
                SET n.last_sync_timestamp = {timestamp}
                RETURN n
                """
        return self.runQuery(query)
    
    def getPlayHistory(self,id,id_type):
        query = f"""
            MATCH (n:{id_type})
            WHERE n.user_uid = "{self.user_uid}" AND n.id = "{id}"
            RETURN n.play_history AS play_history
        """

        result = self.getResultFromDB(query=query,params={"id":id, "id_type":id_type},output_values=['play_history'])
        
        if result is not None:
            return result['play_history'][0]
        else:    
            return False

        return result['play_history'][0]

    def getHourOfDay(self,id,id_type):
        query = f"""
            MATCH (n:{id_type})
            WHERE n.id = "{id}" AND n.user_uid = "{self.user_uid}"
            RETURN n.hour_of_day AS timeOfDay
        """

        result = self.getResultFromDB(query=query,params={"id":id, "id_type":id_type},output_values=['timeOfDay'])
        return result['timeOfDay'][0]

    def makePath(self, pathType, node_id_a, node_id_b, node_type_a, node_type_b):
        query = f"""
            MATCH (a:{node_type_a} {{id: "{node_id_a}"}})
            WHERE a.user_uid = "{self.user_uid}"
            WITH a
            MATCH (b:{node_type_b} {{id: "{node_id_b}"}})
            WHERE b.user_uid = "{self.user_uid}"
            CREATE (a)-[:{pathType}]->(b)
            RETURN a,b
        """

        params = {
            "user_uid": self.user_uid,
            "pathType": pathType,
            "node_id_a": node_id_a,
            "node_id_b": node_id_b,
            "node_type_a": node_type_a,
            "node_type_b": node_type_b,
            "self.user_uid": self.user_uid
        }

        result = self.runQuery(query)

        if result is not None:
            print(f"{pathType} path has been created between node {node_id_a} to {node_id_b}")
            return True
        else:
            print("Query failed but program is continuing.")       
            return False

    def createNode(self,type="",params=None):
    # Will create a new node in neo4j database based on the node_type defined and the data inputted for the selected node
    # All Node Types Require The Following Variables: 
    #   - name: name of the entity (album name, artist name, etc)
    #   - id: a unique id for the entity. Generally, I'm using the Spotify ID in my code for this
    #   - first_seen: when the node was added. Used for tracking time-based trends (when songs were first played, etc)
    #   - last_played: last time the entity was accessed (last time an album was played, last time an artist was listened to, etc)
    #   - times_played: number of times the entity was listened to/played
    #
    # The types of Nodes and their optional parameters are:
    # 1. Track:
    #       - popularity: metric returned by spotify that measures general popularity (from 1 to 100)
    #       - preview_url: url to track that gives short snippet of song
    # 2. Playlist:
    #       - no optional parameters
    # 3. Album:
    #       - release_date: when the album was released
    #       - image_url: url of album image cover
    #       - label: a label describing the album
    # 4. Genre:
    #       - no optional parameters
    # 5. Artist:
    #       - popularity: metric returned by spotify that measures general popularity (from 1 to 100)
    #       - number_followers: number of followers artist has

        match type:
            case "track":
                # Use parameterized queries to prevent Cypher injection
                query = """
                CREATE (n:Track {user_uid: $user_uid, name: $name, id: $id, popularity: $popularity, preview_url: $preview_url, play_history:$play_history, hour_of_day:$hour_of_day})
                """

            case "playlist":
                query = """
                CREATE (n:Playlist {user_uid: $user_uid, name: $name, id: $id, owner_name: $owner_name, num_followers: $num_followers, play_history:$play_history, hour_of_day:$hour_of_day, image_url: $image_url})
                """

            case "album":
                query = """
                CREATE (n:Album {user_uid: $user_uid, name: $name, id: $id, popularity: $popularity, image_url: $image_url, label: $label, play_history:$play_history, hour_of_day:$hour_of_day})
                """

            case "genre":
                query = """
                CREATE (n:Genre {user_uid: $user_uid, name: $name, id: $id, play_history:$play_history, hour_of_day:$hour_of_day})
                """
            
            case "artist":
                query = """
                CREATE (n:Artist {user_uid: $user_uid, name: $name, id:$id, num_followers: $num_followers, image_url: $image_url, popularity: $popularity, play_history:$play_history, hour_of_day:$hour_of_day})
                """
            
            case "config":
                query = """
                CREATE (n:Config {user_uid: $user_uid, name: "configuration", last_sync_timestamp: $last_sync_timestamp, refresh_token: $refresh_token, refresh_token_expired: $refresh_token_expired})
                """
    
            case _:
                print("invalid node_type")
                return
        
        #run query based on node_type
        result = self.runQuery(query,params)

        if result is not None:
            
            if(type != "config"):
                logging.info(f"Created {type} node for {type} named {params['name']} with id {params['id']}")
            else:
                logging.info(f"config node successfully created")
            return True
        else:
            print("Query failed but program is continuing.")       
            return False

    # function checks to see if genre path is previously created. Used to prevent duplicate paths
    # Created when a genre gets updated. 
    def doesPathExist(self,id_start,id_end,start_type,end_type,path_name):
        
        query = f"""
            MATCH (a:{start_type} {{id: "{id_start}"}})
            WHERE a.user_uid = "{self.user_uid}"
            WITH a
            MATCH (b:{end_type} {{id: "{id_end}"}})
            WHERE b.user_uid = "{self.user_uid}"
            RETURN EXISTS((a)-[:{path_name}*1..2]-(b)) AS pathExists
        """

        result = self.getResultFromDB(query=query,params={},output_values=['pathExists'])

        if result is not None:
            if result['pathExists']:
                return result['pathExists'][0]
            else:
                return False
        else:
            print("Query failed but program is continuing.")    
            return False
    
    # function updates genres tied to a node (for albums and artists).
    def updateGenres(self,node_id,genres,node_type,timestamp,hour):

        # For each genre in list
        for genre in genres:

            # if the genre already exists, then update last_played and times_played
            if(self.check_node_exists(id=genre,type="genre")):
                
                # pull last play history from DB
                play_history = self.getPlayHistory(genre,"Genre")
                hour_of_day = self.getHourOfDay(genre,"Genre")

                play_history.append(timestamp)
                hour_of_day.append(hour)

                query = f"""
                    MATCH (n:Genre {{id: "{genre}"}})
                    WHERE n.user_uid = "{self.user_uid}"
                    SET n.play_history={play_history}, n.hour_of_day = {hour_of_day}
                    RETURN n as output
                """
                
                result = self.runQuery(query)

                if result is not None:
                    return result['output'][0]
                else:
                    print("Query failed but program is continuing.")       
                    return False


            # if node doesn't exist, create node
            else:
                params = {
                    "user_uid": self.user_uid,
                    "name": genre,
                    "id": genre,
                    "times_played": 1,
                    "play_history": [timestamp],
                    "hour_of_day":[hour]
                }

                self.createNode(type="genre",params=params)
            
                # connect associated node with path
            
            # if genre node doesn't have a path connected to node, create it
            if not self.doesPathExist(node_id,genre,node_type,"Genre","GENRE"):
                self.makePath("GENRE", node_id, genre,node_type, "Genre")


##################################################################################################

#################################### Other Helper Functions ######################################

# converts a date/time to a UTC timestamp. Used to convert the "played_at" time returned
# to a numeric timestamp instead of a string
def convertTimestamp(timestamp):

    # Parse the string into a datetime object
    dt = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")

    # Set the timezone to UTC
    dt_utc = dt.replace(tzinfo=timezone.utc)

    # get hour of current timezone
    hour = dt.hour


    return int(dt_utc.timestamp()),hour

# returns a dictionary for a track object (see below for descriptions of the fields in the object)
def createTrackDict(name = "", popularity = None, played_at="",context_id="", context_type = "", context_url="",preview_url="",album_id="",album_url="",artists=[]):
    return {
        "name": name,               # name of track
        "album":{
            "id":album_id,
            "url":album_url
        },
        "popularity": popularity,   # metric that tracks popularity of track (ranges from 1-100)
        "played_at": played_at,     # last time user played the track
        "context": {                # context contains info about what initiated the track to be played
            "type": context_type,   # type tells you where the user played the track (ie from an album, playlist, etc)
            "url": context_url,     # the url to the object that was played. For example, if I played from an album, this would be the spotify URL that will return info about it
            "id": context_id
        },
        "preview_url": preview_url, # link to an audio clip that contains a preview of the song
        "artists": artists          # list contains info about the artists attached to album, specifically their ID and URL
    }

# returns an dictionary for an album object (see below for descriptions of the fields in the object)
def createAlbumDict(name="",popularity=None,image_url="",genres=[],label="",artists=[],tracks=[]):
    return {
        "name": name,                   # name of album
        "popularity": popularity,       # metric that albums popularity of track (ranges from 1-100)
        "image_url": image_url,         # url to the image of the album
        "genres": genres,               # a list of strings that describe the genre of the album
        "label": label,                 # a label that describes the album
        "artists": artists,             # list contains info about the artists attached to album, specifically their ID and URL
        "tracks": tracks                # a list containing all the track Ids within the album
    }

# returns an dictionary for an artist object (see below for descriptions of the fields in the object)
def createArtistDict(name="",popularity=None,genres=[],image_url="", num_followers=None):
    return {
        "name": name,                   # name of artist
        "popularity": popularity,       # metric that tracks popularity of artist (ranges from 1-100)
        "genres": genres,               # a list of strings that describe the genre of the artist
        "image_url": image_url,         # url of the image for the artist
        "num_followers": num_followers  # the number followers of the artist
    }

# returns an dictionary for an playlist object (see below for descriptions of the fields in the object)
def createPlaylistDict(name="",description="",num_followers=None,image_url="",owner_name = "", owner_id = "", owner_url = "",tracks=[]):
    return {
        "name": name,                   # name of playlist
        "description": description,     # description of the playlist
        "num_followers": num_followers, # the number followers of the playlist
        "image_url": image_url,         # the url of the image of the playlist
        "owner": {                      # a dictionary that contains info about the user that created the playlist. Contains their name, spotify id, and API URL
            "name": owner_name,
            "id": owner_id,
            "url": owner_url
        },             
        "tracks": tracks                # a list containing the ids of all the tracks within the playlist
    }
'''
Helper function is used to transform API JSONs To a dictionary that is stripped with only the necessary information. 

The "type" parameter is used to tell the function which type of API call is getting fed into the function. The type options are:

1. "track" - will return a dictionary of tracks. Each key is the Spotify ID of the track. See createTrackDict() function for details on the object
2. "album" - will return a dictionary containing info about an album. Each key is the Spotify ID of the album. See createAlbumDict() function for details on the object
3. "artist" - will return a dictionary containing info about an artist. Each key is the Spotify ID of the artist. A single Dictionary Entry has the following layout:
4. "playlist" - will return a dictionary containing info about an album. Each key is the Spotify ID of the album. A single Dictionary Entry has the following layout:

'''
def convertJSON(api_json, type):
    results = {}

    match type:
        case "track":
            #iterate through each track listing, and transform to refined track object
            for track in api_json:
                # initialize artists array to store artists dictionaries
                artists = []

                # Populate artists array to contain id and url of each returned artist
                for artist in track['track']['artists']:
                    artists.append({
                        "id": artist.get("id", ""),
                        "url": artist.get("href", "")
                    })

                if track.get("context") is None:
                    context_type = ""
                    context_url = ""
                    id = ""
                else:
                    context_type = track['context'].get("type","")
                    context_url = track['context'].get("href","")

                    # id is not returned in API response. Below extracts ID from URL
                    id = context_url.split("/")[-1]
            
                results[track['track'].get("id", "")] = createTrackDict(
                    name = track['track'].get("name",""),
                    popularity=track['track'].get("popularity", 0),
                    played_at=track.get("played_at", ""),
                    context_type = context_type,
                    context_url = context_url,
                    context_id = id,
                    preview_url=track['track'].get("preview_url", ""),
                    album_url=track['track']['album'].get("href", ""),
                    album_id=track['track']['album'].get("id", ""),
                    artists = artists
                )
        
        case "album":
            artists = []
            tracks = []

            # Populate artists array to contain id and url of each returned artist
            for artist in api_json.get("artists", []):
                artists.append({
                    "id": artist.get("id", ""),
                    "url": artist.get("href", "")
                })
            
            # Pull IDs of all tracks in album and store in list for later analysis
            for track in api_json.get("tracks", {}).get("items", []):
                tracks.append(track.get("id", ""))
        
            # use function to create new object
            results = createAlbumDict(
                name=api_json.get("name", "Unknown"),
                popularity=api_json.get("popularity", 0),
                image_url = api_json.get("images", [{}])[0].get("url", ""),
                genres=api_json.get("genres", []),
                label=api_json.get("label", "Unknown"),
                artists = artists,
                tracks = tracks
            )
    
        case "artist":

            images = api_json.get("images", [])
            if images and len(images) > 0:
                image_url = images[0].get("url", "")
            else:
                image_url = ""  # or some default value

            # use function to create new object
            results[api_json['id']] = createArtistDict(
                name=api_json.get("name", "Unknown"),
                popularity=api_json.get("popularity", 0),
                genres=api_json.get("genres", []),
                image_url = image_url,
                num_followers=api_json.get("followers", {}).get("total", 0)
            )
        
        case "playlist":
            tracks = []

            # pull all tracks and store their IDs in an array
            for track in api_json.get("tracks", {}).get("items", []):
                if track.get("track"):
                    tracks.append(track['track'].get("id", ""))

            # use function to create new object
            results = createPlaylistDict(
                name=api_json.get("name", ""),
                description=api_json.get("description", ""),
                num_followers=api_json.get("followers", {}).get("total", 0),
                image_url = api_json.get("images", "")[0].get("url", ""),
                owner_name=api_json.get("owner", {}).get("display_name", ""),
                owner_id=api_json.get("owner", {}).get("id", ""),
                owner_url=api_json.get("owner", {}).get("href", ""),
                tracks = tracks
            )
        
    return results

# function is used to send errors to mpalmail@protonmail.com to ensure service is running as expected
def mailtrap_error_handler(main_func):
    def wrapper(*args, **kwargs):
        try:
            return main_func(*args, **kwargs)
        except Exception as e:
            # Gets the name of the function that raised the error
            function_name = main_func.__name__  

            # Create Error Message
            error_msg = f"Error in main(): {e}\n\nTraceback:\n{traceback.format_exc()}"
            print(error_msg)
            
            # Prepare the email message
            msg = MIMEText(error_msg)
            msg['Subject'] = f"Spotify App Error in {function_name}"
            msg['From'] = sender
            msg['To'] = receiver

            # Send the email using Mailtrap
            with smtplib.SMTP("live.smtp.mailtrap.io", 587) as server:
                server.starttls()
                server.login("api", st.secrets['mailtrap']['api_token'])
                server.sendmail(sender, receiver, msg.as_string())
            return None
    return wrapper

# the main function that pulls API data into DB. Made separate from main function so that it can be run in main app file.
def API2DB(user_uid, access_token = "", refresh_token="", utc_timestamp="",my_bar = None):
     # Configure the logger
    logging.basicConfig(
        filename=LOGGING_FILEPATH,                               # Log file name
        level=logging.DEBUG,                                # Log level
        format='%(asctime)s - %(levelname)s - %(message)s'  # Log format
    )
    print("In API2DB Function")

    # instantiate apiHelper and neo4jHelper to make API and DB calls
    apiManager = apiHelper("",headers = None, params = None)
    neo4jManager = Neo4jHelper(user_uid=user_uid)

    # check if config node (used to store metadata/tokens) exists in DB. If not, create it
    if(access_token == ""):
        refresh_token = neo4jManager.getRefreshTokenFromDB()

        # exit function if refresh token is not in database
        if refresh_token == "":
            return

        access_token,refresh_token = apiManager.getRefreshToken(Neo4jManager=neo4jManager,refresh_token=neo4jManager.getRefreshTokenFromDB())
          
    # get current UTC timestamp if timestamp isn't given
    if(utc_timestamp == ""):
        # Get the current UTC time
        current_utc_time = datetime.now(timezone.utc)

        # Convert to a UNIX timestamp
        utc_timestamp = current_utc_time.timestamp()
    
    # Create Auth header w/ access token
    headers = {
        'Authorization': f"Bearer {access_token}",
    }

    #if access token isn't able to get pulled, throw an error and store metric in DB to tell front end that refresh token is expired
    if access_token is False:
        st.error('Error: access token was unable to be pulled.. this likely means the refresh token is expired. Rerun so user can reauthorize', icon=":material/sentiment_dissatisfied:")
        if neo4jManager.storeRefreshTokenExpired(True):
            print("refresh token expired state successfully stored")
        else:
            print("Error: Refresh Token Expired State not successfully stored")

        return "refreshTokenExpired"
    else:
        neo4jManager.storeRefreshTokenExpired(False)

    # set the header value
    apiManager.headers = headers

    # pull recently played tracks and convert ta a stripped version
    recently_played_data = apiManager.getAPIResponse(type = "recently_played")

    if(recently_played_data == 0):
        return
    else:
        recently_played_tracks = convertJSON(recently_played_data,"track")

    logging.info(f"Successfully Pulled Recently Played List. There are {len(recently_played_tracks)} tracks in the result")
    

    # get the timestamp associated with the last play time for the song most recently played in DB
    last_sync_timestamp = neo4jManager.getTimestamp()

    if my_bar is not None:
        # used to adjust progress bar
        progress_delta = int(100 / len(recently_played_tracks))

        # passed along to progress bar to denote how much progress has passed
        progress_value = 0

    # ETL code. Extracts API info, converts it into objects that are then loaded to Graph Database
    for track in list(recently_played_tracks.keys()):
        played_at_timestamp, hour = convertTimestamp(recently_played_tracks[track]['played_at'])

        # if the song's played at time is older than the last seen play time, then skip it
        # used to ensure there is no overlap between syncs
        if(last_sync_timestamp < played_at_timestamp):

            logging.info(f"name of current track: {recently_played_tracks[track]['name']}")
            logging.info(f"track play time: {played_at_timestamp}")

            #if the track already exists in DB, then overwrite its popularity, last played, preview_url, and iterate times played
            if(neo4jManager.check_node_exists(track,"track")):

                # pull track play history from node in DB
                play_history = neo4jManager.getPlayHistory(track,"Track")
                hour_of_day = neo4jManager.getHourOfDay(track,"Track")

                # append new values to arrays
                play_history.append(played_at_timestamp)
                hour_of_day.append(hour)

                popularity = recently_played_tracks[track]['popularity']
                preview_url = recently_played_tracks[track]['preview_url']

                query = f"""
                    MATCH (n:Track {{id: "{track}"}})
                    WHERE n.user_uid = "{user_uid}"
                    SET n.popularity={popularity}, n.preview_url="{preview_url}", n.play_history={play_history}, n.hour_of_day={hour_of_day}
                    RETURN n as output
                """
                
                result = neo4jManager.runQuery(query)

                if result is not None:
                    print(f"Query Succeeded: {result}")
                else:
                    print("Query failed but program is continuing.")       
                
            else:
                # if the track doesn't exist, create a new node
                params = {
                    "user_uid": user_uid,
                    "name": recently_played_tracks[track]['name'],
                    "id": track,
                    "play_history": [played_at_timestamp],
                    "hour_of_day":[hour],
                    "popularity": recently_played_tracks[track]['popularity'],
                    "preview_url": recently_played_tracks[track]['preview_url'],
                }

                neo4jManager.createNode(params=params,type="track")

            # pull album info
            album_results = apiManager.getAPIResponse("album",url=recently_played_tracks[track]['album']['url'])

            # if an error is returned when making call.. skip storing in DB
            if(album_results == 0):
                logging.error(f"Received an Error When Making API Call.. Skipping The entry for {artist}")
            else:
                album_results = convertJSON(album_results,"album")

                album_id = recently_played_tracks[track]['album']['id']
                logging.info(f"making API Call to Pull Data For Album With ID {album_id}")

                # if album exists, update params
                if(neo4jManager.check_node_exists(album_id,"album")):
                    
                    # pull album play history
                    play_history = neo4jManager.getPlayHistory(album_id, "Album")
                    hour_of_day = neo4jManager.getHourOfDay(album_id, "Album")

                    # append values to arrays
                    play_history.append(played_at_timestamp)
                    hour_of_day.append(hour)

                    image_url = album_results['image_url']
                    label = album_results['label']
                    popularity = album_results['popularity']

                    query = f"""
                        MATCH (n:Album {{id: "{album_id}"}})
                        WHERE n.user_uid = "{user_uid}"
                        SET n.image_url="{image_url}", n.label="{label}", n.popularity={popularity}, n.image_url="{image_url}", n.play_history={play_history}, n.hour_of_day={hour_of_day}
                        RETURN n as output
                    """

                    result = neo4jManager.runQuery(query)

                    if result is not None:
                        print(f"Query Succeeded: {result}")
                    else:
                        print("Query failed but program is continuing.")       

                else:
                    params = {
                        "user_uid": user_uid,
                        "name": album_results['name'],
                        "id": album_id,
                        "image_url": album_results['image_url'],
                        "label": album_results['label'],
                        "play_history": [played_at_timestamp],
                        "hour_of_day": [hour],
                        "popularity": album_results['popularity'],
                    }

                    # Create new node and connect track to album in DB
                    neo4jManager.createNode("album",params)

                # create path from track to album
                if not neo4jManager.doesPathExist(track,album_id,"Track","Album","IN_ALBUM"):
                    neo4jManager.makePath("IN_ALBUM",track,album_id,"Track","Album")

                # create/update Genre Node based on genres associated with album
                neo4jManager.updateGenres(node_id=album_id,genres=album_results['genres'],node_type="genre",timestamp=played_at_timestamp,hour=hour)
                    

            # iterate through all artists
            for artist in recently_played_tracks[track]['artists']:
                
                # pull id of artist
                artist_id = artist['id']
                logging.info(f"making API Call to Pull Data For Artist With ID {artist_id}")

                # Make API Call to pull artist info and convert to stripped down JSON object
                artist_results = apiManager.getAPIResponse("artist",url=artist['url'])

                # if an error is returned when making call.. skip storing in DB
                if(artist_results == 0):
                    logging.error(f"Received an Error When Making API Call.. Skipping The entry for {artist}")

                else:
                    artist_results = convertJSON(artist_results,"artist")
                    
                    # if the artist exists, update its parameters. If it doesn't, create a new node and make path to track
                    if (neo4jManager.check_node_exists(artist_id, "artist")):

                        # pull artists play history from node in DB
                        play_history = neo4jManager.getPlayHistory(artist_id,"Artist")
                        hour_of_day = neo4jManager.getHourOfDay(artist_id,"Artist")

                        # append value to arrays
                        play_history.append(played_at_timestamp)
                        hour_of_day.append(hour)

                        popularity = artist_results[artist_id]['popularity']
                        image_url = artist_results[artist_id]['image_url']
                        num_followers =  artist_results[artist_id]['num_followers']
                        
                        # Update artist fields
                        query = f"""
                            MATCH (n:Artist {{id: "{artist_id}"}})
                            WHERE n.user_uid = "{user_uid}"
                            SET n.popularity={popularity}, n.image_url="{image_url}", n.num_followers="{num_followers}", n.play_history={play_history}, n.hour_of_day={hour_of_day}
                            RETURN n
                        """

                        result = neo4jManager.runQuery(query)

                        if result is not None:
                            print(f"Query Succeeded: {result}")
                        else:
                            print("Query failed")  

                    else:

                        params = {
                            "user_uid": user_uid,
                            "name":artist_results[artist_id]['name'],
                            "popularity": artist_results[artist_id]['popularity'],
                            "id": artist_id,
                            "image_url": artist_results[artist_id]['image_url'],
                            "num_followers": artist_results[artist_id]['num_followers'],
                            "play_history": [played_at_timestamp],
                            "hour_of_day": [hour]
                        }

                        # create artist node
                        neo4jManager.createNode(params=params,type="artist")

                    # create path from track to artist
                    if not neo4jManager.doesPathExist(track,artist_id,"Track","Artist","MADE_BY"):
                        neo4jManager.makePath("MADE_BY",track,artist_id,"Track","Artist")
                    
                    # create path from album to artist
                    if not neo4jManager.doesPathExist(album_id,artist_id,"Album","Artist","MADE_BY"):
                        neo4jManager.makePath("MADE_BY",album_id,artist_id,"Album","Artist")

                    # update the genres associated with artist
                    neo4jManager.updateGenres(node_id = artist_id,genres = artist_results[artist_id]['genres'],node_type="Artist",timestamp=played_at_timestamp,hour=hour)
            
            
            # if the person played a track through a playlist, add/modify playlist in DB
            if(recently_played_tracks[track]['context']['type'] == "playlist"):
                
                # pull id of playlist
                playlist_id = recently_played_tracks[track]['context']['id']

                logging.info(f"making API Call to Pull Data For Playlist With ID {playlist_id}")

                # make API call to get additional info about playlist
                playlist_results = apiManager.getAPIResponse("playlist",url=recently_played_tracks[track]['context']['url'])

                if(playlist_results == 0):
                    logging.error(f"Received an Error When Making API Call.. Skipping The entry for {playlist_id}")
                else:
                    playlist_results = convertJSON(playlist_results,"playlist")

                    if(neo4jManager.check_node_exists(playlist_id,"playlist")):
                        
                        # pull playlist play history from node in DB
                        play_history = neo4jManager.getPlayHistory(playlist_id, "Playlist")
                        hour_of_day = neo4jManager.getHourOfDay(playlist_id, "Playlist")

                        #append next value to these arrays
                        play_history.append(played_at_timestamp)
                        hour_of_day.append(hour)

                        num_followers = playlist_results['num_followers']
                        description = playlist_results['description']
                        image_url = playlist_results['image_url']

                        query = f"""
                            MATCH (n:Playlist {{id: "{playlist_id}"}})
                            WHERE n.user_uid = "{user_uid}"
                            SET n.num_followers={num_followers}, n.play_history={play_history}, n.hour_of_day={hour_of_day}, n.image_url="{image_url}", n.description="{description}", n.image_url="{image_url}"
                            RETURN n
                        """

                        result = neo4jManager.runQuery(query)

                        if result is not None:
                            print(f"Query Succeeded: {result}")
                        else:
                            print("Query failed")  

                    
                    # if not in DB create node
                    else:
                        params={
                            "user_uid": user_uid,
                            "name": playlist_results['name'],
                            "description": playlist_results['description'],
                            "num_followers": playlist_results['num_followers'],
                            "image_url": playlist_results['image_url'],
                            "owner_name": playlist_results['owner']['name'],
                            "id": playlist_id,
                            "play_history": [played_at_timestamp],
                            "hour_of_day": [hour]
                        }

                        result = neo4jManager.createNode("playlist",params)

                    if not neo4jManager.doesPathExist(track,playlist_id,"Track","Playlist","IN_PLAYLIST"):
                        neo4jManager.makePath("IN_PLAYLIST",track,playlist_id,"Track","Playlist")

        # set progress bar if loading from streamlit app                
        if my_bar is not None:
            progress_value = progress_value + progress_delta
            my_bar.progress(progress_value, text = "Loading Tracks... Please Wait")

    # store timestamp of last synced song in DB
    neo4jManager.storeTimestamp(int(utc_timestamp))

    # remove refresh and acccess token from memory
    refresh_token = ""
    access_token = ""

                        
    
############################################################################################

#################################### Main Function #################################

# run function that pulls data for all user containers. Will be main script that runs on web app container daily
@mailtrap_error_handler
def main():

    # pull all users from DB. Will then iterate through all, and if refresh token exists, update the db for that user
    client = MongoClient(f"""mongodb://{st.secrets['user_database']['username']}:{quote_plus(f"{st.secrets['user_database']['password']}")}@localhost:27017/{st.secrets['user_database']['database_name']}""")
    db = client['userDB']
    collection = db['listings']

    results = collection.find({}, {"email": 1, "user_uid":1, "_id": 0})  # Exclude `_id`

    # Convert to a list of dictionaries
    data_list = list([doc for doc in results])

    client.close()
    
    
    for doc in data_list:
        print(f"updating DB for user {doc['user_uid']}")
        API2DB(user_uid = doc['user_uid'])


############################################################################################
#instantiate special variable
if __name__=="__main__":
    main()
