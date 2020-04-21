import discord
import pickle
import requests
import json
import asyncio
import datetime
import pytz
import time
#import sys
from config import *
from pprint import pprint

# Config file 'config.py' is needed with 3 lines:
# discord_key = (valid discord API key)
# twitch_key = (valid twitch API key)
# master_id = discord ID of desired superuser, ideally whoever is running the bot

# How long in seconds to wait before deleting the offline message
offline_del_time = 60 * 15

# Messing around with custom exceptions...
class TooFewArgumentsError(Exception):
    pass


class NoSuchStreamerError(Exception):
    pass


class BadRequestError(Exception):
    pass


class ChannelAlreadyPresentError(Exception):
    pass


class NoStreamerNotificationsError(Exception):
    pass


class GrynnBot(discord.Client):
    def __init__(self):
        self.is_background_task_running = False
        self.data = {}
        self.master_id = master_id
        self.online = set()
        self.all_streamers = set()
        self.new_headers = {'Client-ID': twitch_key}
        self.v5_headers = {'Accept': 'application/vnd.twitchtv.v5+json' ,'Client-ID': twitch_key}
        super().__init__()
    
    # Client Events
    async def on_ready(self):
        print('Logged in as')
        print(client.user.name)
        print(client.user.id)
        print('------')
        
        self.read_data()
        self.loop.create_task(self.background_loop())
        
    async def on_message(self, message):
        # Master Commands
        # print(message.author.permissions_in(message.channel).manage_channels)
        if message.author.id == self.master_id:
            # Kill Grynnbot
            if message.content.lower() == "!kill_grynnbot":
                self.write_data()
                await self.shut_down()
            
            if message.content.lower() == "!reset_data":
                self.data = {"streamers": []}
                self.write_data()
                await message.channel.send("Data reset")

        # Manage Channel Commands (+ Master)
        if message.content.lower().startswith("!add_stream"):
            # Add Streamer
            if message.author.permissions_in(message.channel).manage_channels or message.author.id == self.master_id:
                # Try/Except to catch errors with adding a streamer, and providing proper outputs
                out_message = ""
                try:
                    self.add_streamer(message.content, message.channel)
                    out_message = "Stream successfully added! Notification messages will be sent in this channel."
                except TooFewArgumentsError:
                    out_message = "Too few arguments, command usage: `!add_stream <stream_name> [optional message]`"\
                                + "\nExample: `!add_stream just_Grynn Stream's up everyone!`"
                except NoSuchStreamerError as nsse:
                    out_message = "An error has occured: no streamer can be found with the name `" + nsse.args[0] + "`"
                except BadRequestError as bre:
                    out_message = "There was a problem with the request: `Status Code: " + bre.args[0] + "`"
                except ChannelAlreadyPresentError:
                    out_message = "This channel is already tracking that streamer."
                except Exception as e:
                    out_message = "An unexpected error has occured:\n`" + str(type(e)) + " : " + str(e) + "`"
                    raise e
                finally:
                    await message.channel.send(out_message, delete_after=15)

        if message.content.lower().startswith("!remove_stream"):
            if message.author.permissions_in(message.channel).manage_channels or message.author.id == self.master_id:
                out_message = ""
                try:
                    self.remove_streamer(message.content, message.channel)
                    out_message = "Stream successfully removed. Notification messages will no longer be sent in this channel."
                except TooFewArgumentsError:
                    out_message = "Too few arguments, command usage: `!remove_stream <stream_name>`"\
                                + "\nExample: `!remove_stream just_Grynn`"
                except NoStreamerNotificationsError:
                    out_message = "That stream is not being tracked in this channel."
                finally:
                    await message.channel.send(out_message, delete_after=15)
    # Bot Functions
    
    #Remove Streamer
    def remove_streamer(self, content, channel):
        message_args = content.split(" ")
        # case: no args given
        if len(message_args) < 2:
            raise TooFewArgumentsError
        # case: args given
        else:
            # normalize name
            stream_name = message_args[1].lower()
            self.remove_streamer_data(stream_name, channel.id)
            
    # remove streamer data
    def remove_streamer_data(self, name, channel):
        index = self.get_streamer_index(name)
        # If streamer is present...
        if index > -1:
            channel_index = self.get_channel_index(index, channel)
            # if channel receiving notifications
            if channel_index > -1:
                self.data["streamers"][index]["outputs"].pop(channel_index)
            else:
                raise NoStremerNotificationsError()
        # If streamer isn't even there.
        else:
            raise NoStreamerNotificationsError()
        self.write_data()
    
    # get channel index
    def get_channel_index(self, streamer_index, channel):
        streamer_data = self.data["streamers"][streamer_index]
        for i in range(len(streamer_data["outputs"])):
            if streamer_data["outputs"][i]["channel_id"] == channel:
                return i
        return -1
    
    # Add streamer
    def add_streamer(self, content, channel):
        message_args = content.split(" ")
        # case: no args given
        if len(message_args) < 2:
            raise TooFewArgumentsError
        # case: args given
        else:
            # normalize name
            stream_name = message_args[1].lower()
            # construct custom message
            custom_message = " "
            if len(message_args) > 2:
                custom_message = " ".join(message_args[2:])
            # update data
            self.update_streamer_data(stream_name, channel.id, custom_message)
            
    # update streamer data
    def update_streamer_data(self, name, channel, message):
        # start constructing streamer object
        streamer = {}
        # add the channel ID and output message
        new_output = {"channel_id": channel, "output_message": message, "current_message": None}
        # find the index of the streamer in data, if present
        index = self.get_streamer_index(name)
        # case: streamer already in list
        if index > -1:
            #if the channel isn't in the array already
            if not any(d["channel_id"] == channel for d in self.data["streamers"][index]["outputs"]):
                self.data["streamers"][index]["outputs"].append(new_output)
            else:
                raise ChannelAlreadyPresentError()
        # case: new streamer
        else:
            stream_id = self.get_streamer_id(name)
            streamer = {"name": name ,"id": stream_id, "outputs":[new_output]}
            self.data["streamers"].append(streamer)
        self.write_data()        

    # get_streamer_index
    def get_streamer_index(self, name):
        _name = name
        for i in range(len(self.data["streamers"])):
            streamer = self.data["streamers"][i]
            if streamer["name"] == _name:
                return i
        return -1
    
    # Get Streamer ID
    def get_streamer_id(self, name):
        _name = name.lower()
        # form request URL
        url = 'https://api.twitch.tv/helix/users?login=' + _name
        # get request
        req_response = requests.get(url, headers=self.new_headers)
        # if response is bad, raise exception
        if req_response.status_code != 200:
            raise BadRequestError(req_response.status_code)
        # otherwise
        else:
            # extract the 'data' array
            res_data = req_response.json()['data']
            # if the array is empty, then that name has no id
            if len(res_data) == 0:
                raise NoSuchStreamerError(name)
            # if everything is good, return the id
            else:
                streamer_info = res_data[0]
                return streamer_info["id"]

    # Shut Down
    async def shut_down(self):
        print("Shutting down from command")
        await client.close()
        
    # Read data
    # reads data from pickle file. Should only run on startup
    def read_data(self):
        try:
            with open("data.pkl", "rb") as infile:
                self.data = pickle.load(infile)
        except Exception as e:
            print("Problem reading from data.pkl")
            print(e)
            
    # Write data
    # writes data to the pickle file. Should occur upon any changes
    # should also occur on shutdown
    def write_data(self):
        try:
            with open("data.pkl", "wb") as outfile:
                pickle.dump(self.data, outfile)
        except Exception as e:
            print("Problem writing to data.pkl")
            print(e)
            
    # get all streamers set
    def get_all_streamers_set(self):
        streamers_set = set()
        for streamer in self.data["streamers"]:
            streamers_set.add(streamer["id"])
        return streamers_set
    
    # Notify
    # This is the "main" background task, subtasks branch from it
    # notify process:
    #     have set of all streamers
    #     have set of previously online streamers
    #     generate set of currently online streamers from twitch api
    #     p - c = newly offline
    #     c - p = newly online
    #     for each newly offline streamer
    #         delete online message
    #         post "streamer has gone offline" message (self delete 1 hour)
    #     for each newly online streamer
    #         delete offline message if present
    #         post stream notification
    async def notify(self):
        # get all streamers
        self.all_streamers = self.get_all_streamers_set()
        # set the prviously online
        previously_online = self.online
        # get the list of all online stream objects
        online_list = self.get_online_streams_list()
        # Instantiate the currently online set
        current_online = set()
        # Fill the currently online set with ids
        for stream in online_list:
            current_online.add(stream["channel"]["_id"])
        # find out who's newly on or off
        newly_online = current_online - previously_online
        newly_offline = previously_online - current_online
        
        if len(newly_offline) > 0:
            await self.post_offline_messages(newly_offline)
        
        if len(newly_online) > 0:
            await self.post_online_messages(newly_online, online_list)
        
        # Set online to the current set
        self.online = current_online
        #pass

    # post offline messages
    # takes set of stream ids as argument
    async def post_offline_messages(self, ids):
        for i in range(len(self.data["streamers"])):
            streamer = self.data["streamers"][i]
            for sid in ids:
                if streamer["id"] == str(sid):
                    for j in range(len(streamer["outputs"])):
                        output_info = streamer["outputs"][j]
                        out_channel = self.get_channel(output_info["channel_id"])
                        if output_info["current_message"] != None:
                            try:
                                old_message = await out_channel.fetch_message(output_info["current_message"])
                                await old_message.delete()
                            except Exception as e:
                                print("Could not find old message, will not delte it")
                        out_message = streamer["name"] + " has gone offline."
                        new_message = await out_channel.send(out_message, delete_after = offline_del_time)
                        self.data["streamers"][i]["outputs"][j]["current_message"] = new_message.id
                        self.write_data()
                        
    # post_online_messages
    async def post_online_messages(self, ids, response_list):
        for i in range(len(self.data["streamers"])):
            streamer = self.data["streamers"][i]
            for sid in ids:
                if streamer["id"] == str(sid):
                    rich_emb = {}
                    for stream_data in response_list:
                        if str(sid) == str(stream_data["channel"]["_id"]):
                            rich_emb = self.generate_rich_embed(stream_data)
                    for j in range(len(streamer["outputs"])):
                        output_info = streamer["outputs"][j]
                        out_channel = self.get_channel(output_info["channel_id"])
                        if output_info["current_message"] != None:
                            try:
                                old_message = await out_channel.fetch_message(output_info["current_message"])
                                await old_message.delete()
                            except Exception as e:
                                print("Could not find message, not trying to delete")
                        out_message = output_info["output_message"]
                        
                        new_message = await out_channel.send(out_message, embed=rich_emb)
                        self.data["streamers"][i]["outputs"][j]["current_message"] = new_message.id
                        self.write_data()

    # generate_rich_embed
    def generate_rich_embed(self, stream_data):
        now = pytz.utc.localize(datetime.datetime.utcnow())
        timezone = pytz.timezone("America/Chicago")
        now_adjusted = now.astimezone(timezone)
        
        channel_data = stream_data["channel"]
        channel_name = channel_data["display_name"]
        logo = channel_data["logo"]
        title = channel_data["status"]
        game = channel_data["game"]
        if game == "":
            game = "No game selected"
        followers = channel_data["followers"]
        views = channel_data["views"]
        profile_banner_url = channel_data["profile_banner"]
        
        emb = discord.Embed(title=channel_name + " is now online on Twitch!",
              url="https://www.twitch.tv/"+channel_name,
              color=0xBA55D3)
        emb.set_thumbnail(url=logo)
        emb.add_field(name="Title:", value=title, inline=False)
        emb.add_field(name="Now Playing:", value=game, inline=False)
        emb.add_field(name="Followers:", value=followers)
        emb.add_field(name="Total Views:", value=views)
        emb.set_author(name="Stream Notification Service", icon_url=self.user.avatar_url)
        emb.set_footer(text="⏰" + now_adjusted.strftime("%Y-%m-%d %H:%M:%S %Z"))
        if not (profile_banner_url is None):
            emb.set_image(url=profile_banner_url)
            
        return emb

    # get online streamers
    def get_online_streams_list(self):
        req_url = 'https://api.twitch.tv/kraken/streams/?channel='
        for id in self.all_streamers:
            req_url = req_url + str(id) + ","
        req_url = req_url[:-1]
        try:
            status_request = requests.get(req_url, headers=self.v5_headers)
            # From the returned data, we put it in the array.
            status_array = status_request.json()['streams']
            #pprint(status_array)
            return status_array
        except Exception as e:
            print(e)
            return []
        

    # Background loop
    async def background_loop(self):
        await self.wait_until_ready()
        if not self.is_background_task_running:
            self.is_background_task_running = True
            print("No background task found, starting up")
            while not self.is_closed():
                print("staring notify at " + str(datetime.datetime.now().time()))
                await self.notify()
                await asyncio.sleep(60)
        else:
            print("Background task detected, not starting")

client = GrynnBot()
client.run(discord_key)
