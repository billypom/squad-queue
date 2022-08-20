import discord
from discord.ext import commands, tasks
from discord.ui import Button, View
import vlog_msg
import json
from dateutil.parser import parse
from datetime import datetime, timedelta
import collections
import time
import DBA
import secretly
import urllib.parse
import shutil
import subprocess
import requests
import math

with open('./config.json', 'r') as cjson:
            config = json.load(cjson)

CHECKMARK_ADDITION = "-\U00002713"
CHECKMARK_ADDITION_LEN = 2
Lounge = [461383953937596416]
time_print_formatting = "%B %d, %Y at %I:%M%p EDT"
#There are two timezones: the timezone your staff schedules events in, and your server's timezone
#Set this to the number of hours ahead (or behind) your staff's timezone is from your server's timezone
#This is so that you don't have to adjust your machine clock to accomodate for your staff

#For example, if my staff is supposed to schedule events in EST and my machine is PST, this number would be 3 since EST is 3 hours ahead of my machine's PST
# STAFF SCHEDULE IN EDT, MY MACHINE IN UTC, NUMBER IS -4
TIME_ADJUSTMENT = timedelta(hours=config["TIME_ADJUSTMENT"])

#number of minutes before scheduled time that queue should open
QUEUE_OPEN_TIME = timedelta(minutes=config["QUEUE_OPEN_TIME"])

#number of minutes after QUEUE_OPEN_TIME that teams can join the mogi
JOINING_TIME = timedelta(minutes=config["JOINING_TIME"])

#number of minutes after JOINING_TIME for any potential extra teams to join
EXTENSION_TIME = timedelta(minutes=config["EXTENSION_TIME"])

Scheduled_Event = collections.namedtuple('Scheduled_Event', 'size time started mogi_channel')


class Mogi(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
            
        # no commands should work when self.started or self.gathering is False, 
        # except for start, which initializes each of these values.
        self.started = False
        self.gathering = False
        self.making_rooms_run = False
        
        # can either be 2, 3, or 4, representing the respective mogi sizes
        self.size = 2
        
        # self.waiting is a list of dictionaries, with the keys each corresponding to a
        # Discord member class, and the values being a list with 2 values:
        # index 0 being the player's confirmation status, and index 1 being the player's MMR.
        self.waiting = []
        
        # self.list is also a list of dictionaries, with the keys each corresponding to a
        # Discord member class, and the values being the player's MMR.
        self.list = []
        
        # contains the avg MMR of each confirmed team
        self.avgMMRs = []

        #list of Channel objects created by the bot for easy deletion
        self.categories = []
        self.channels = []
        
        self.scheduled_events = []

        self.is_automated = False

        self.mogi_channel = None

        self.start_time = None

        self._scheduler_task = self.sqscheduler.start()

        self._msgqueue_task = self.send_queued_messages.start()

        self.msg_queue = []

    async def lockdown(self, channel:discord.TextChannel):
        overwrite = channel.overwrites_for(channel.guild.default_role)
        overwrite.send_messages = False
        await channel.set_permissions(channel.guild.default_role, overwrite=overwrite)
        await channel.send("Locked down " + channel.mention)

    async def unlockdown(self, channel:discord.TextChannel):
        overwrite = channel.overwrites_for(channel.guild.default_role)
        overwrite.send_messages = None
        await channel.set_permissions(channel.guild.default_role, overwrite=overwrite)
        await channel.send("Unlocked " + channel.mention)

    async def scheduler_mogi_start(self):
        """Functions that tries to launch scheduled mogis - Note that it won't launch any sscheduled mogis
        if an there is already a mogi ongoing, instead it will send an error message and delete that event from the schedule"""

        cur_time = datetime.now()

        to_remove = [] #Keep a list of indexes to remove - can't remove while iterating
        for ind, event in enumerate(self.scheduled_events):
            if (event.time - QUEUE_OPEN_TIME) < cur_time:
                #if self.started or self.gathering: #We can't start a new event while the current event is already going
                if self.gathering:
                    to_remove.append(ind)
                    await event.mogi_channel.send(f"Because there is an ongoing event right now, the following event has been removed: {self.get_event_str(event)}\n")
                else:
                    if self.started:
                        await self.endMogi()
                    to_remove.append(ind)
                    await self.launch_mogi(event.mogi_channel, event.size, True, event.time)
                    await self.unlockdown(event.mogi_channel)

        for ind in reversed(to_remove):
            del self.scheduled_events[ind]

    async def ongoing_mogi_checks(self):
            #If it's not automated, not started, we've already started making the rooms, don't run this
            if not self.is_automated or not self.started or self.making_rooms_run:
                return

            cur_time = datetime.now()
            if (self.start_time - QUEUE_OPEN_TIME + JOINING_TIME + EXTENSION_TIME) <= cur_time:
                await self.makeRoomsLogic(self.mogi_channel, (self.start_time.minute)%60, True)
                return

            if self.start_time - QUEUE_OPEN_TIME + JOINING_TIME <= cur_time:
                #check if there are an even amount of teams since we are past the queue time
                numLeftoverTeams = len(self.list) % int((12/self.size))
                if numLeftoverTeams == 0:
                    await self.makeRoomsLogic(self.mogi_channel, (self.start_time.minute)%60, True)
                    return
                else:
                    if int(cur_time.second / 20) == 0:
                        force_time = self.start_time - QUEUE_OPEN_TIME + JOINING_TIME + EXTENSION_TIME
                        minutes_left = int((force_time - cur_time).seconds/60)
                        x_teams = int(int(12/self.size) - numLeftoverTeams)
                        await self.mogi_channel.send(f"Need {x_teams} more team(s) to start immediately. Starting in {minutes_left} minute(s) regardless.")

    

    @tasks.loop(seconds=20.0)
    async def sqscheduler(self):
        """Scheduler that checks if it should start mogis and close them"""
        #It may seem silly to do try/except Exception, but this coroutine **cannot** fail
        #This coroutine *silently* fails and stops if exceptions aren't caught - an annoying abtraction of asyncio
        #This is unacceptable considering people are relying on these mogis to run, so we will not allow this routine to stop
        try:
            await self.scheduler_mogi_start()
        except Exception as e:
            print(e)

        try:
            await self.ongoing_mogi_checks()
        except Exception as e:
            print(e)

    async def queue_or_send(self, ctx, msg, delay=0):
        if config["queue_messages"] is True:
            self.msg_queue.append(msg)
        else:
            sentmsg = await ctx.send(msg)
            if delay > 0:
                await sentmsg.delete(delay=delay)

    @tasks.loop(seconds=config["sec_between_queue_msgs"])
    async def send_queued_messages(self):
        mogi_channel = self.get_mogi_channel()
        if mogi_channel is not None:
            if len(self.msg_queue) > 0:
                sentmsgs = []
                sentmsg = ""
                for i in range(len(self.msg_queue)-1, -1, -1):
                    sentmsg = self.msg_queue.pop(i) + "\n" + sentmsg
                    if len(sentmsg) > 1500:
                        #await mogi_channel.send(sentmsg)
                        sentmsgs.append(sentmsg)
                        sentmsg = ""
                #else:
                if len(sentmsg) > 0:
                    #await mogi_channel.send(sentmsg)
                    sentmsgs.append(sentmsg)
                for i in range(len(sentmsgs)-1, -1, -1):
                    await mogi_channel.send(sentmsgs[i])

    def get_mogi_channel(self):
        mogi_channel_id = config["mogichannel"]
        return self.bot.get_channel(mogi_channel_id)

    

    # the 4 functions below act as various checks for each of the bot commands.
    # if any of these are false, sends a message to the channel
    # and throws an exception to force the command to stop

    async def hasroles(self, ctx):
        for rolename in config["roles"]:
            for role in ctx.author.roles:
                if role.name == rolename:
                    return
        raise commands.MissingAnyRole(config["roles"])

    async def is_mogi_channel(self, ctx):
        if ctx.channel.id != config["mogichannel"]:
            await(await ctx.send("You cannot use this command in this channel!")).delete(delay=5)
            raise Exception()

    async def is_started(self, ctx):
        if self.started == False:
            await(await ctx.send("Mogi has not been started yet.. type !start")).delete(delay=5)
            raise Exception()

    async def is_gathering(self, ctx):
        if self.gathering == False:
            await(await ctx.send("Mogi is closed; players cannot join or drop from the event")).delete(delay=5)
            raise Exception()
        
            

    # Checks if a user is in a squad currently gathering players;
    # returns False if not found, and returns the squad index in
    # self.waiting if found
    async def check_waiting(self, member: discord.Member):
        if(len(self.waiting) == 0):
            return False
        for i in range(len(self.waiting)):
            for player in self.waiting[i].keys():
                # for testing, it's convenient to change player.id
                # and member.id to player.display_name
                # and member.display_name respectively
                # (lets you test with only 2 accounts and changing
                #  nicknames)
                if player.id == member.id:
                    return i
        return False

    # Checks if a user is in a full squad that has joined the mogi;
    # returns False if not found, and returns the squad index in
    # self.list if found
    async def check_list(self, member: discord.Member):
        if (len(self.list) == 0):
            return False
        for i in range(len(self.list)):
            for player in self.list[i].keys():
                # for testing, it's convenient to change player.id
                # and member.id to player.display_name
                # and member.display_name respectively
                # (lets you test with only 2 accounts and changing
                #  nicknames)
                if player.id == member.id:
                    return i
        return False
    
    @commands.command()
    async def qwe(self, ctx):
        await self.queue_or_send(ctx, 'queue or send qwe')
        return

    @commands.command(aliases=['c'])
    @commands.max_concurrency(number=1,wait=True)
    @commands.guild_only()
    async def can(self, ctx, members: commands.Greedy[discord.Member]):
        """Tag your partners to invite them to a mogi or accept a invitation to join a mogi"""
        try:
            await Mogi.is_mogi_channel(self, ctx)
            await Mogi.is_started(self, ctx)
            await Mogi.is_gathering(self, ctx)
        except:
            return

        if (len(members) > 0 and len(members) < self.size - 1):
            await self.queue_or_send(ctx, "%s didn't tag the correct number of people for this format (%d)"
                            % (ctx.author.display_name, self.size-1), delay=5)
            return

        sheet = self.bot.get_cog('Sheet')

        # checking if message author is already in the mogi
        # checkWait = await Mogi.check_waiting(self, ctx.author.display_name)
        # checkList = await Mogi.check_list(self, ctx.author.display_name)
        checkWait = await Mogi.check_waiting(self, ctx.author)
        checkList = await Mogi.check_list(self, ctx.author)
        if checkWait is not False:
            if self.waiting[checkWait][ctx.author][0] == True:
                await self.queue_or_send(ctx, "%s has already confirmed for this event; type `!d` to drop"
                                         % (ctx.author.display_name), delay=5)  
                return
        if checkList is not False:
            await self.queue_or_send(ctx, "%s has already confirmed for this event; type `!d` to drop"
                                     % (ctx.author.display_name), delay=5)  
            return

        # logic for when no players are tagged
        if len(members) == 0:
            #runs if message author has been invited to squad
            #but hasn't confirmed
            if checkWait is not False:
                self.waiting[checkWait][ctx.author][0] = True
                confirmedPlayers = []
                missingPlayers = []
                for player in self.waiting[checkWait].keys():
                    if self.waiting[checkWait][player][0] == True:
                        confirmedPlayers.append(player)
                    else:
                        missingPlayers.append(player)
                string = ("%s has confirmed for their squad [%d/%d]\n"
                          % (ctx.author.display_name, len(confirmedPlayers), self.size))
                if len(missingPlayers) > 0:
                          string += "Missing players: "
                          string += ", ".join([player.display_name for player in missingPlayers])
                
                #if player is the last one to confirm for their squad,
                #add them to the mogi list
                if len(missingPlayers) == 0:
                    squad = self.waiting[checkWait]
                    squad2 = {}
                    teamMsg = ""
                    totalMMR = 0
                    playerCount = 1
                    for player in squad.keys():
                        playerMMR = int(squad[player][1])
                        squad2[player] = playerMMR
                        totalMMR += playerMMR
                        teamMsg += "`%d.` %s (%d MMR)\n" % (playerCount, player.display_name, int(playerMMR))
                        playerCount += 1
                    self.avgMMRs.append(int(totalMMR/self.size))
                    self.waiting.pop(checkWait)
                    self.list.append(squad2)
                    if len(self.list) > 1:
                        s = "s"
                    else:
                        s = ""
                    string += ("`Squad successfully added to mogi list [%d team%s]`:\n%s"
                                   % (len(self.list), s, teamMsg))
                await self.queue_or_send(ctx, string)
                await self.ongoing_mogi_checks()
                return

            await self.queue_or_send(ctx, "%s didn't tag the correct number of people for this format (%d)"
                            % (ctx.author.display_name, self.size-1), delay=5)
            return

        # Input validation for tagged members; checks if each tagged member is already
        # in a squad, as well as checks if any of them are duplicates
        for member in members:
            checkWait = await Mogi.check_waiting(self, member)
            checkList = await Mogi.check_list(self, member)
            if checkWait is not False or checkList is not False:
                msg = ("%s is already confirmed for a squad for this event `("
                               % (member.display_name))
                if checkWait is not False:
                    msg += ", ".join([player.display_name for player in self.waiting[checkWait].keys()])
                else:
                    msg += ", ".join([player.display_name for player in self.list[checkList].keys()])
                msg += ")` They should type `!d` if this is in error."
                await self.queue_or_send(ctx, msg)
                return
            if member == ctx.author:
                await self.queue_or_send(ctx, "%s, Duplicate players are not allowed for a squad, please try again"
                                         % (ctx.author.mention))
                return
        if len(set(members)) < len(members):
            await self.queue_or_send(ctx, "%s, Duplicate players are not allowed for a squad, please try again"
                                     % (ctx.author.mention))
            return
            
        # logic for when the correct number of arguments are supplied
        # (self.size - 1)
        players = {ctx.author: [True]}
        lookupMembers = [ctx.author.display_name]
        lookupMembers += [member.display_name for member in members]
        # playerMMR = await sheet.mmr(ctx.author)
        playerMMRs = []
        # for i in range(len(lookupMembers)):
        #     with DBA.DBAccess() as db:
        #         temp = db.query('SELECT mmr FROM player WHERE player_name = %s;', (lookupMembers[i],))
        #     playerMMRs.append(temp[0][0])
        playerMMRs = await sheet.mmr(lookupMembers)
        if playerMMRs[0] is False:
            await self.queue_or_send(ctx, "Error: MMR for player %s cannot be found! Please contact a staff member for help"
                                     % ctx.author.display_name, delay=10)
            return
        players[ctx.author].append(playerMMRs[0])
        for i in range(self.size-1):
            players[members[i]] = [False]
            #playerMMR = await sheet.mmr(members[i])
            if playerMMRs[i+1] is False:
                await self.queue_or_send(ctx, "Error: MMR for player %s cannot be found! Please contact a staff member for help"
                                         % members[i].display_name, delay=10)
                return
            players[members[i]].append(playerMMRs[i+1])
        self.waiting.append(players)
        
        msg = "%s has created a squad with " % ctx.author.display_name
        msg += ", ".join([player.display_name for player in members])
        msg += "; each player must type `!c` to join the queue [1/%d]\n" % (self.size)
        await self.queue_or_send(ctx, msg, delay=10)


           
    @commands.command(aliases=['d'])
    @commands.max_concurrency(number=1,wait=True)
    @commands.guild_only()
    @commands.cooldown(1, 15, commands.BucketType.member)
    async def drop(self, ctx):
        """Remove your squad from a mogi"""
        try:
            await Mogi.is_mogi_channel(self, ctx)
            await Mogi.is_started(self, ctx)
            await Mogi.is_gathering(self, ctx)
        except:
            return

        checkWait = await Mogi.check_waiting(self, ctx.author)
        checkList = await Mogi.check_list(self, ctx.author)
        # "is" instead of "==" is essential here, otherwise if
        # i=0 is returned, it will think it's False
        if checkWait is False and checkList is False:
            await self.queue_or_send(ctx, "%s is not currently in a squad for this event; type `!c @partnerNames`"
                                     % (ctx.author.display_name), delay=5)
            return
        if checkWait is not False:
            droppedTeam = self.waiting.pop(checkWait)
            fromStr = " from unfilled squads"
        else:
            droppedTeam = self.list.pop(checkList)
            self.avgMMRs.pop(checkList)
            fromStr = " from mogi list"
        string = "Removed team "
        string += ", ".join([player.display_name for player in droppedTeam.keys()])
        string += fromStr
        await self.queue_or_send(ctx, string, delay=5)

    @commands.command(aliases=['r'])
    @commands.max_concurrency(number=1,wait=True)
    @commands.guild_only()
    async def remove(self, ctx, num: int):
        """Removes the given squad ID from the mogi list"""
        await Mogi.hasroles(self, ctx)
        try:
            await Mogi.is_mogi_channel(self, ctx)
            await Mogi.is_started(self, ctx)
        except:
            return
        if num > len(self.list) or num < 1:
            await(await ctx.send("Invalid squad ID; there are %d squads in the mogi"
                                 % len(self.list))).delete(delay=10)
            return
        squad = self.list.pop(num-1)
        self.avgMMRs.pop(num-1)
        await ctx.send("Removed squad %s from mogi list"
                       % (", ".join([player.display_name for player in squad.keys()])))

    #The caller is responsible to make sure the paramaters are correct
    async def launch_mogi(self, mogi_channel:discord.TextChannel, size: int, is_automated=False, start_time=None):       
        self.started = True
        self.gathering = True
        self.making_rooms_run = False
        self.is_automated = is_automated
        self.size = size
        self.waiting = []
        self.list = []
        self.avgMMRs = []

        if not is_automated:
            self.is_automated = False
            self.mogi_channel = None
            self.start_time = None
        else:
            self.is_automated = True
            self.mogi_channel = mogi_channel
            self.start_time = start_time

        await mogi_channel.send("A %dv%d mogi has been started - @here Type `!c`, `!d`, or `!list`" % (size, size))

    @commands.command()
    @commands.guild_only()
    async def start(self, ctx, size: int):
        """Start a mogi in the channel defined by the config file"""
        await Mogi.hasroles(self, ctx)
        try:
            await Mogi.is_mogi_channel(self, ctx)
        except:
            return
        if not await Mogi.start_input_validation(ctx, size):
            return False
        self.is_automated = False
        await self.launch_mogi(ctx.channel, size)

    @commands.command()
    @commands.guild_only()
    async def close(self, ctx):
        """Close the mogi so players can't join or drop"""
        await Mogi.hasroles(self, ctx)
        try:
            await Mogi.is_mogi_channel(self, ctx)
            await Mogi.is_started(self, ctx)
            await Mogi.is_gathering(self, ctx)
        except:
            return
        self.gathering = False
        self.is_automated = False
        await ctx.send("Mogi is now closed; players can no longer join or drop from the event")

    @commands.command()
    @commands.guild_only()
    async def open(self, ctx):
        """Reopen the mogi so that players can join and drop"""
        await Mogi.hasroles(self, ctx)
        try:
            await Mogi.is_mogi_channel(self, ctx)
            await Mogi.is_started(self, ctx)
        except:
            return
        if self.gathering is True:
            await(await ctx.send("Mogi is already open; players can join and drop from the event")
                  ).delete(delay=5)
            return
        self.gathering = True
        self.is_automated = False
        await ctx.send("Mogi is now open; players can join and drop from the event")

    async def deleteChannels(self):
        for i in range(len(self.channels)-1, -1, -1):
            try:
                await self.channels[i][0].delete()
                self.channels.pop(i)
            except:
                pass
        for i in range(len(self.categories)-1, -1, -1):
            try:
                await self.categories[i].delete()
                self.categories.pop(i)
            except:
                pass  

    async def endMogi(self):
        await self.deleteChannels()
        self.started = False
        self.gathering = False
        self.making_rooms_run = False
        self.is_automated = False
        self.mogi_channel = None
        self.start_time = None
        self.waiting = []
        self.list = []
        self.avgMMRs = []

    @commands.command()
    async def remakeRooms(self, ctx, openTime:int):
        self.making_rooms_run = False
        await self.deleteChannels()
        await self.makeRooms(ctx, openTime)

    @commands.command()
    @commands.guild_only()
    async def end(self, ctx):
        """End the mogi"""
        await Mogi.hasroles(self, ctx)
        try:
            await Mogi.is_mogi_channel(self, ctx)
            await Mogi.is_started(self, ctx)
        except:
            return
        await self.endMogi()
        await ctx.send("%s has ended the mogi" % ctx.author.display_name)
            

    @commands.command(aliases=['l'])
    @commands.cooldown(1, 120)
    @commands.guild_only()
    async def list(self, ctx, mmrorder=""):
        """Display the list of confirmed squads for a mogi; sends 15 at a time to avoid
           reaching 2000 character limit"""
        try:
            await Mogi.is_mogi_channel(self, ctx)
            await Mogi.is_started(self, ctx)
        except:
            return
        mogilist = self.list
        avgMMRs = self.avgMMRs
        if mmrorder.lower() == "mmr":
            indexes = range(len(self.avgMMRs))
            sortTeamsMMR = sorted(zip(self.avgMMRs, indexes), reverse=True)
            avgMMRs = [x for x, _ in sortTeamsMMR]
            mogilist = [self.list[i] for i in (x for _, x in sortTeamsMMR)]
        if len(mogilist) == 0:
            await(await ctx.send("There are no squads in the mogi - confirm %d players to join" % (self.size))).delete(delay=5)
            return
        msg = "`Mogi List`\n"
        for i in range(len(mogilist)):
            #safeguard against potentially reaching 2000-char msg limit
            if len(msg) > 1500:
                await ctx.send(msg)
                msg = ""
            msg += "`%d.` " % (i+1)
            msg += ", ".join([player.display_name for player in mogilist[i].keys()])
            msg += " (%d MMR)\n" % (avgMMRs[i])
        if(len(self.list) % (12/self.size) != 0):
            msg += ("`[%d/%d] teams for %d full rooms`"
                    % ((len(mogilist) % (12/self.size)), (12/self.size), int(len(mogilist) / (12/self.size))+1))
        await ctx.send(msg)

    @commands.command()
    @commands.cooldown(1, 30, commands.BucketType.member)
    @commands.guild_only()
    async def squad(self, ctx):
        """Displays information about your squad for a mogi"""
        try:
            await Mogi.is_mogi_channel(self, ctx)
            await Mogi.is_started(self, ctx)
        except:
            return
        checkWait = await Mogi.check_waiting(self, ctx.author)
        checkList = await Mogi.check_list(self, ctx.author)
        if checkWait is False and checkList is False:
            await self.queue_or_send(ctx, "%s is not currently in a squad for this event; type `!c @partnerNames`"
                                     % (ctx.author.display_name), delay=5)
            return
        msg = ""
        playerNum = 1
        if checkWait is not False:
            myTeam = self.waiting[checkWait]
            listString = ""
            confirmCount = 0
            for player in myTeam.keys():
                listString += ("`%d.` %s (%d MMR)" % (playerNum, player.display_name, int(myTeam[player][1])))
                if myTeam[player][0] is False:
                    listString += " `✘ Unconfirmed`\n"
                else:
                    listString += " `✓ Confirmed`\n"
                    confirmCount += 1
                playerNum += 1
            msg += ("`%s's squad [%d/%d confirmed]`\n%s"
                    % (ctx.author.display_name, confirmCount, self.size, listString))

            await self.queue_or_send(ctx, msg, delay=30)
        else:
            myTeam = self.list[checkList]
            msg += ("`%s's squad [registered]`\n" % (ctx.author.display_name))
            for player in myTeam.keys():
                msg += ("`%d.` %s (%d MMR)\n"
                        % (playerNum, player.display_name, int(myTeam[player])))
                playerNum += 1
            await self.queue_or_send(ctx, msg, delay=30)

    @commands.command()
    @commands.guild_only()
    async def sortTeams(self, ctx):
        """Backup command if !makerooms doesn't work; doesn't make channels, just sorts teams in MMR order"""
        await Mogi.hasroles(self, ctx)
        try:
            await Mogi.is_mogi_channel(self, ctx)
            await Mogi.is_started(self, ctx)
        except:
            return
        indexes = range(len(self.avgMMRs))
        sortTeamsMMR = sorted(zip(self.avgMMRs, indexes), reverse=True)
        sortedMMRs = [x for x, _ in sortTeamsMMR]
        sortedTeams = [self.list[i] for i in (x for _, x in sortTeamsMMR)]
        msg = "`Sorted list`\n"
        for i in range(len(sortedTeams)):
            if i > 0 and i % 15 == 0:
                await ctx.send(msg)
                msg = ""
            msg += "`%d.` " % (i+1)
            msg += ", ".join([player.display_name for player in sortedTeams[i].keys()])
            msg += " (%d MMR)\n" % sortedMMRs[i]
        await ctx.send(msg)

    async def makeRoomsLogic(self, mogi_channel:discord.TextChannel, openTime:int, startedViaAutomation=False):
        """Sorts squads into rooms based on average MMR, creates room channels and adds players to each room channel"""
        if self.making_rooms_run and startedViaAutomation: #Reduce race condition, but also allow manual !makeRooms
            return
        if startedViaAutomation:
            await self.lockdown(mogi_channel)
        
        self.making_rooms_run = True
        catNum = config["channels_per_category"]
        if self.gathering:
            self.gathering = False
            await mogi_channel.send("Mogi is now closed; players can no longer join or drop from the event")

        numRooms = int(len(self.list) / (12/self.size))
        if numRooms == 0:
            await mogi_channel.send("Not enough players to fill a room! Try this command with at least %d teams" % int(12/self.size))
            return

        if openTime >= 60 or openTime < 0:
            await mogi_channel.send("Please specify a valid time (in minutes) for rooms to open (00-59)")
            return
        penTime = openTime + 5
        startTime = openTime + 10
        while penTime >= 60:
            penTime -= 60
        while startTime >= 60:
            startTime -= 60
            
        numTeams = int(numRooms * (12/self.size))
        finalList = self.list[0:numTeams]
        finalMMRs = self.avgMMRs[0:numTeams]

        indexes = range(len(finalMMRs))
        sortTeamsMMR = sorted(zip(finalMMRs, indexes), reverse=True)
        sortedMMRs = [x for x, _ in sortTeamsMMR]
        sortedTeams = [finalList[i] for i in (x for _, x in sortTeamsMMR)]
        for i in range(int(numRooms/catNum)+1):
            cat = await mogi_channel.guild.create_category_channel(name="Rooms %d" % (i+1),
                                                                   position=config["channel_category_position"])
            self.categories.append(cat)
        for i in range(numRooms):

            #creating room roles and channels
            roomName = "Room %d" % (i+1)
            #category = mogi_channel.category
            category = self.categories[int(i/catNum)]
            overwrites = {
                mogi_channel.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                mogi_channel.guild.me: discord.PermissionOverwrite(read_messages=True)
            }
            
            #tries to retrieve all these roles, and add them to the
            #channel overwrites if the role specified in the config file exists
            for bot_role_id in config["roles_for_channels"]:
                bot_role = mogi_channel.guild.get_role(bot_role_id)
                if bot_role is not None:
                    overwrites[bot_role] = discord.PermissionOverwrite(read_messages=True)
            

            msg = "`%s`\n" % roomName
            for j in range(int(12/self.size)):
                index = int(i * 12/self.size + j)
                msg += "`%d.` " % (j+1)
                msg += ", ".join([player.display_name for player in sortedTeams[index].keys()])
                msg += " (%d MMR)\n" % sortedMMRs[index]
                for player in sortedTeams[index].keys():
                    overwrites[player] = discord.PermissionOverwrite(read_messages=True)
            roomMsg = msg
            mentions = ""
            scoreboard = "Table: `!scoreboard %d " % (12/self.size)
            for j in range(int(12/self.size)):
                index = int(i * 12/self.size + j)
                mentions += " ".join([player.mention for player in sortedTeams[index].keys()])
                mentions += " "
                scoreboard += ",".join([player.display_name for player in sortedTeams[index].keys()])
                if j+1 < int(12/self.size):
                    scoreboard += ","
            
            roomMsg += "%s`\n" % scoreboard
            roomMsg += ("\nDecide a host amongst yourselves; room open at :%02d, penalty at :%02d, start by :%02d. \nUse !table to submit to the results channel\nGood luck!\n\n"
                        % (openTime, penTime, startTime))
            roomMsg += mentions
            try:
                roomChannel = await category.create_text_channel(name=roomName, overwrites=overwrites)
                self.channels.append([roomChannel, False])
                await roomChannel.send(roomMsg)
            except Exception as e:
                errMsg = f"\nAn error has occurred while creating the room channel; please contact your opponents in DM or another channel\n"
                errMsg += mentions
                msg += errMsg
            await mogi_channel.send(msg)
            
        if numTeams < len(self.list):
            missedTeams = self.list[numTeams:len(self.list)]
            missedMMRs = self.avgMMRs[numTeams:len(self.list)]
            msg = "`Late teams:`\n"
            for i in range(len(missedTeams)):
                msg += "`%d.` " % (i+1)
                msg += ", ".join([player.display_name for player in missedTeams[i].keys()])
                msg += " (%d MMR)\n" % missedMMRs[i]
            await mogi_channel.send(msg)


    @commands.command()
    @commands.bot_has_guild_permissions(manage_channels=True)
    @commands.guild_only()
    async def lockerdown(self, ctx):
        # git er dun
        mogi_channel = self.get_mogi_channel()
        await self.lockdown(mogi_channel)

    @commands.command()
    @commands.bot_has_guild_permissions(manage_channels=True)
    @commands.guild_only()
    async def unlockerdown(self, ctx):
        # git er undun
        mogi_channel = self.get_mogi_channel()
        await self.unlockdown(mogi_channel)

    @commands.command()
    @commands.bot_has_guild_permissions(manage_channels=True)
    @commands.guild_only()
    async def makeRooms(self, ctx, openTime: int):
        await Mogi.hasroles(self, ctx)
        try:
            await Mogi.is_mogi_channel(self, ctx)
            await Mogi.is_started(self, ctx)
        except:
            return
        await self.makeRoomsLogic(ctx.channel, openTime)


    @commands.command()
    @commands.bot_has_guild_permissions(manage_channels=True)
    @commands.guild_only()
    async def finish(self, ctx):
        """Finishes the room by adding a checkmark to the channel. Anyone in the room can call this command."""
        current_channel = ctx.channel
        for index, (channel, isFinished) in enumerate(self.channels):
            if current_channel == channel:
                if not isFinished:
                    await current_channel.edit(name=current_channel.name + CHECKMARK_ADDITION)
                    self.channels[index] = [current_channel, True]
       
       
    @staticmethod
    async def start_input_validation(ctx, size:int):
        valid_sizes = [2, 3, 4, 6]
        if size not in valid_sizes:
            await(await ctx.send("The size you entered is invalid; proper values are: 2, 3, 4")).delete(delay=5)
            return False
        
        return True 
                   
                                      
    @commands.command()
    @commands.guild_only()
    async def schedule(self, ctx, size: int, *, schedule_time:str):
        """Schedules a room in the future so that the staff doesn't have to be online to open the mogi and make the rooms"""
        
        await Mogi.hasroles(self, ctx)
        
        if not await Mogi.start_input_validation(ctx, size):
            return False
              
        try:
            actual_time = parse(schedule_time)
            gabagoo = parse(schedule_time)
            actual_time = actual_time - TIME_ADJUSTMENT
            queue_time = actual_time - QUEUE_OPEN_TIME
            print(f'actual time: {type(actual_time)} | {actual_time}')
            print(f'queue time: {type(queue_time)} | {queue_time}')
            mogi_channel = self.get_mogi_channel()
            guild = self.bot.get_guild(Lounge[0])
            if mogi_channel == None:
                await ctx.send("I can't see the mogi channel, so I can't schedule this event.")
                return
            event = Scheduled_Event(size, actual_time, False, mogi_channel)
            
            self.scheduled_events.append(event)
            self.scheduled_events.sort(key=lambda data:data.time)
            try:
                await guild.create_scheduled_event(name=f'SQ:{str(size)}v{str(size)} Gathering', start_time=queue_time, end_time=actual_time, location="#sq-join")
            except Exception as e:
                print(e)
                await ctx.send('Cannot schedule event in the past')
                return
            #await ctx.send(f"popuko actual time: {gabagoo} | popuko adjustment {TIME_ADJUSTMENT} | popu post adjust {actual_time} || Scheduled {Mogi.get_event_str(event)}")
            await ctx.send(f"Scheduled {Mogi.get_event_str(event)}")
        except (ValueError, OverflowError):
            await ctx.send("I couldn't figure out the date and time for your event. Try making it a bit more clear for me.")

    @commands.command(aliases=['pt'])
    async def parsetime(self, ctx, *, schedule_time:str):
        try:
            actual_time = parse(schedule_time)
            gabagoo = parse(schedule_time)
            actual_time = actual_time - TIME_ADJUSTMENT
            #await ctx.send(f"popuko actual time: {gabagoo} | popuko adjustment {TIME_ADJUSTMENT} | popu post adjust {actual_time} | ```<t:" + str(int(time.mktime(actual_time.timetuple()))) + ":F>``` -> <t:" + str(int(time.mktime(actual_time.timetuple()))) + ":F>")
            await ctx.send(f"```<t:" + str(int(time.mktime(actual_time.timetuple()))) + ":F>``` -> <t:" + str(int(time.mktime(actual_time.timetuple()))) + ":F>")
        except (ValueError, OverflowError):
            await ctx.send("I couldn't figure out the date and time for your event. Try making it a bit more clear for me.")
        
    @commands.command()
    @commands.guild_only()
    async def view_schedule(self, ctx):
        """Displays the schedule"""
        await Mogi.hasroles(self, ctx)
        
        if len(self.scheduled_events) == 0:
            await ctx.send("There are currently no schedule events. Do `!schedule` to schedule a future event.")
        else:
            event_str = ""
            for ind, this_event in enumerate(self.scheduled_events, 1):
                event_str += f"`{ind}.` {Mogi.get_event_str(this_event)}\n"
            event_str += "\nDo `!remove_event` to remove that event from the schedule."
            await ctx.send(event_str)
            
    @commands.command()
    @commands.guild_only()
    @commands.max_concurrency(number=1,wait=True)
    async def remove_event(self, ctx, event_num: int):
        """Removes an event from the schedule"""
        await Mogi.hasroles(self, ctx)
        
        if event_num < 1 or event_num > len(self.scheduled_events):
            await ctx.send("This event number isn't in the schedule. Do `!view_schedule` to see the scheduled events.")
        else:
            removed_event = self.scheduled_events.pop(event_num-1)
            await ctx.send(f"Removed `{event_num}.` {self.get_event_str(removed_event)}")
    
        
    @staticmethod
    def get_event_str(this_event):
        event_size, event_time = this_event.size, this_event.time
        timezone_adjusted_time = event_time + TIME_ADJUSTMENT
        event_time_str = timezone_adjusted_time.strftime(time_print_formatting)
        return f"{event_size}v{event_size} on {event_time_str}"

    @commands.command()
    @commands.guild_only()
    @commands.max_concurrency(number=1, wait=True)
    async def table(self, ctx, mogi_format: int, *scores):
        # print('Im table')
        # TODO: change this in production
        SQ_TIER_ID = 965286774098260029

        # Create list
        score_list = list(scores)
        print(score_list)

        bad = await self.check_if_banned_characters(score_list)
        if bad:
            await self.queue_or_send(ctx, f'Invalid input. There must be 12 players and 12 scores.')
            return

        # score_string = str(scores) #.translate(remove_chars)
        # score_list = score_string.split()

        # Check for 12 players
        # print(score_list)
        # print(len(score_list))
        if len(score_list) == 24:
            pass
        else:
            await self.queue_or_send(ctx, f'Invalid input. There must be 12 players and 12 scores.')
            return
        
        # Replace playernames with playerids
        # Create list
        #print(f'score list: {score_list}')
        player_list_check = []
        for i in range(0, len(score_list), 2):
            with DBA.DBAccess() as db:
                temp = db.query('SELECT player_id FROM player WHERE player_name = %s;', (score_list[i],))
                print(f'{i} | {score_list[i]} | {temp[0][0]}')
                player_list_check.append(score_list[i])
                score_list[i] = temp[0][0]


        
        
        # Check for duplicate players
        has_dupes = await self.check_for_dupes_in_list(player_list_check)
        if has_dupes:
            await self.queue_or_send(ctx, '``Error 37:`` You cannot have duplicate players on a table')
            return

        # Check the mogi_format
        if mogi_format == 1:
            SPECIAL_TEAMS_INTEGER = 63
            OTHER_SPECIAL_INT = 19
            MULTIPLIER_SPECIAL = 2.1
        elif mogi_format == 2:
            SPECIAL_TEAMS_INTEGER = 142
            OTHER_SPECIAL_INT = 39
            MULTIPLIER_SPECIAL = 3.0000001
        elif mogi_format == 3:
            SPECIAL_TEAMS_INTEGER = 288
            OTHER_SPECIAL_INT = 59
            MULTIPLIER_SPECIAL = 3.1
        elif mogi_format == 4:
            SPECIAL_TEAMS_INTEGER = 402
            OTHER_SPECIAL_INT = 79
            MULTIPLIER_SPECIAL = 3.35
        elif mogi_format == 6:
            SPECIAL_TEAMS_INTEGER = 525
            OTHER_SPECIAL_INT = 99
            MULTIPLIER_SPECIAL = 3.5
        else:
            await self.queue_or_send(ctx, f'``Error 27:`` Invalid format: {mogi_format}. Please use 1, 2, 3, 4, or 6.')
            return

        # Initialize a list so we can group players and scores together
        player_score_chunked_list = list()
        for i in range(0, len(score_list), 2):
            player_score_chunked_list.append(score_list[i:i+2])
        # print(f'player score chunked list: {player_score_chunked_list}')

        # Chunk the list into groups of teams, based on mogi_format and order of scores entry
        chunked_list = list()
        for i in range(0, len(player_score_chunked_list), mogi_format):
            chunked_list.append(player_score_chunked_list[i:i+mogi_format])
        
        # Get MMR data for each team, calculate team score, and determine team placement
        mogi_score = 0
        # print(f'length of chunked list: {len(chunked_list)}')
        # print(f'chunked list: {chunked_list}')
        for team in chunked_list:
            temp_mmr = 0
            team_score = 0
            count = 0
            for player in team:
                try:
                    with DBA.DBAccess() as db:
                        # This part makes sure that only players in the current channel's lineup can have a table made for them
                        # WRONG. I removed lineups JOIN from this statement because sq xd.
                        temp = db.query('SELECT mmr FROM player WHERE player_id = %s;', (player[0],))
                        if temp[0][0] is None:
                            mmr = 0
                        else:
                            mmr = temp[0][0]
                            count+=1
                        temp_mmr += mmr
                        try:
                            team_score += int(player[1])
                        except Exception:
                            score_and_pen = str(player[1]).split('-')
                            team_score = team_score + int(score_and_pen[0]) - int(score_and_pen[1])
                except Exception as e:
                    # check for all 12 players exist
                    await self.send_to_debug_channel(ctx, e)
                    await self.queue_or_send(ctx, f'``Error 24:`` There was an error with the following player: <@{player[0]}>')
                    return
            # print(team_score)
            if count == 0:
                count = 1
            team_mmr = temp_mmr/count
            team.append(team_score)
            team.append(team_mmr)
            mogi_score += team_score
        # Check for 984 score
        if mogi_score == 984:
            pass
        else:
            await self.queue_or_send(ctx, f'``Error 28:`` `Scores = {mogi_score} `Scores must add up to 984.')
            return

        # Sort the teams in order of score
        # [[players players players], team_score, team_mmr]
        sorted_list = sorted(chunked_list, key = lambda x: int(x[len(chunked_list[0])-2]))
        sorted_list.reverse() 
        # print(f'sorted list: {sorted_list}')

        # Create hlorenzi string
        lorenzi_query=''

        # Initialize score and placement values
        prev_team_score = 0
        prev_team_placement = 1
        team_placement = 0
        count_teams = 1
        for team in sorted_list:
            # If team score = prev team score, use prev team placement, else increase placement and use placement
            # print('if team score == prev team score')
            # print(f'if {team[len(team)-2]} == {prev_team_score}')
            if team[len(team)-2] == prev_team_score:
                team_placement = prev_team_placement
            else:
                team_placement = count_teams
            count_teams += 1
            team.append(team_placement)
            if mogi_format != 1:
                lorenzi_query += f'{team_placement} #AAC8F4 \n'
            for idx, player in enumerate(team):
                if idx > (mogi_format-1):
                    continue
                with DBA.DBAccess() as db:
                    temp = db.query('SELECT player_name, country_code FROM player WHERE player_id = %s;', (player[0],))
                    player_name = temp[0][0]
                    country_code = temp[0][1]
                    score = player[1]
                lorenzi_query += f'{player_name} [{country_code}] {score}\n'

            # Assign previous values before leaving
            prev_team_placement = team_placement
            prev_team_score = team[len(team)-3]

        # Request a lorenzi table
        query_string = urllib.parse.quote(lorenzi_query)
        url = f'https://gb.hlorenzi.com/table.png?data={query_string}'
        response = requests.get(url, stream=True)
        with open(f'/home/sq/squad_queue_v2/images/{hex(ctx.author.id)}table.png', 'wb') as out_file:
            shutil.copyfileobj(response.raw, out_file)
        del response

        # Ask for table confirmation
        # table_view = Confirm(ctx.author.id)
        channel = self.bot.get_channel(ctx.channel.id)
        await channel.send(file=discord.File(f'/home/sq/squad_queue_v2/images/{hex(ctx.author.id)}table.png'), delete_after=300)

        await channel.send('Is this table correct? :thinking: (Type `yes` or `no`)', delete_after=300)

        
        try:
            lorenzi_response = await self.bot.wait_for('message', check=lambda message: message.author == ctx.author, timeout=60)
            if lorenzi_response.content.lower() not in ['yes', 'y']:
                await self.queue_or_send(ctx, 'Table denied. Try again.')
                return
        except Exception as e:
            await self.send_to_debug_channel(ctx, e)
            await self.queue_or_send(ctx, 'No response from reporter. Timed out')
            return
        
        
            # if table_view.value is None:
            # await ctx.send('No response from reporter. Timed out')
        db_mogi_id = 0
        # Create mogi
        with DBA.DBAccess() as db:
            db.execute('INSERT INTO mogi (mogi_format, tier_id) values (%s, %s);', (mogi_format, SQ_TIER_ID))

        # Get the results channel and tier name for later use
        with DBA.DBAccess() as db:
            temp = db.query('SELECT results_id, tier_name FROM tier WHERE tier_id = %s;', (SQ_TIER_ID,))
            db_results_channel = temp[0][0]
            tier_name = temp[0][1]
        results_channel = self.bot.get_channel(db_results_channel)

        # Pre MMR table calculate
        value_table = list()
        for idx, team_x in enumerate(sorted_list):
            working_list = list()
            for idy, team_y in enumerate(sorted_list):
                pre_mmr = 0.0
                if idx == idy: # skip value vs. self
                    pass
                else:
                    team_x_mmr = team_x[len(team_x)-2]
                    team_x_placement = team_x[len(team_x)-1]
                    team_y_mmr = team_y[len(team_y)-2]
                    team_y_placement = team_y[len(team_y)-1]
                    if team_x_placement == team_y_placement:
                        pre_mmr = (SPECIAL_TEAMS_INTEGER*((((team_x_mmr - team_y_mmr)/9998)**2)**(1/3))**2)
                        if team_x_mmr >= team_y_mmr:
                            pass
                        else: #team_x_mmr < team_y_mmr:
                            pre_mmr = pre_mmr * -1
                    else:
                        if team_x_placement > team_y_placement:
                            pre_mmr = (1 + OTHER_SPECIAL_INT*(1 + (team_x_mmr-team_y_mmr)/9998)**MULTIPLIER_SPECIAL)
                        else: #team_x_placement < team_y_placement
                            pre_mmr = -(1 + OTHER_SPECIAL_INT*(1 + (team_y_mmr-team_x_mmr)/9998)**MULTIPLIER_SPECIAL)
                working_list.append(pre_mmr)
            value_table.append(working_list)

        # # DEBUG
        # print(f'\nprinting value table:\n')
        # for _list in value_table:
        #     print(_list)

        # Actually calculate the MMR
        for idx, team in enumerate(sorted_list):
            temp_value = 0.0
            for pre_mmr_list in value_table:
                for idx2, value in enumerate(pre_mmr_list):
                    if idx == idx2:
                        temp_value += value
                    else:
                        pass
            team.append(math.ceil(temp_value))

        # Create mmr table string
        if mogi_format == 1:
            string_mogi_format = 'FFA'
        else:
            string_mogi_format = f'{str(mogi_format)}v{str(mogi_format)}'

        mmr_table_string = f'<big><big>SQ     {string_mogi_format}</big></big>\n'
        mmr_table_string += f'PLACE |       NAME       |  MMR  |  +/-  | NEW MMR |  RANKUPS\n'

        for team in sorted_list:
            my_player_place = team[len(team)-2]
            string_my_player_place = str(my_player_place)
            for idx, player in enumerate(team):
                mmr_table_string += '\n'
                if idx > (mogi_format-1):
                    break
                with DBA.DBAccess() as db:
                    temp = db.query('SELECT player_name, mmr, peak_mmr, rank_id FROM player WHERE player_id = %s;', (player[0],))
                    my_player_name = temp[0][0]
                    my_player_mmr = temp[0][1]
                    my_player_peak = temp[0][2]
                    my_player_rank_id = temp[0][3]
                    if my_player_peak is None:
                        # print('its none...')
                        my_player_peak = 0
                my_player_score = int(player[1])
                my_player_new_rank = ''

                # PLACEMENTS WILL NEVER BE IN SQ
                # # Place the placement players
                # placement_name = ''
                # if my_player_mmr is None:
                #     if my_player_score >=111:
                #         my_player_mmr = 5250
                #         placement_name = 'Gold'
                #     elif my_player_score >= 81:
                #         my_player_mmr = 3750
                #         placement_name = 'Silver'
                #     elif my_player_score >= 41:
                #         my_player_mmr = 2250
                #         placement_name = 'Bronze'
                #     else:
                #         my_player_mmr = 1000
                #         placement_name = 'Iron'
                #     with DBA.DBAccess() as db:
                #         temp = db.query('SELECT rank_id FROM ranks WHERE placement_mmr = %s;', (my_player_mmr,))
                #         init_rank = temp[0][0]
                #         db.execute('UPDATE player SET base_mmr = %s, rank_id = %s WHERE player_id = %s;', (my_player_mmr, init_rank, player[0]))
                #     await channel.send(f'<@{player[0]}> has been placed at {placement_name} ({my_player_mmr} MMR)')

                # if is_sub: # Subs only gain on winning team
                #     if team[len(team)-1] < 0:
                #         my_player_mmr_change = 0
                #     else:
                #         my_player_mmr_change = team[len(team)-1]
                # else:
                #     my_player_mmr_change = team[len(team)-1]

                my_player_mmr_change = team[len(team)-1]
                my_player_new_mmr = (my_player_mmr + my_player_mmr_change)

                # Start creating string for MMR table
                mmr_table_string += f'{string_my_player_place.center(6)}|'
                mmr_table_string +=f'{my_player_name.center(18)}|'
                mmr_table_string += f'{str(my_player_mmr).center(7)}|'

                # Check sign of mmr delta
                if my_player_mmr_change >= 0:
                    temp_string = f'+{str(my_player_mmr_change)}'
                    string_my_player_mmr_change = f'{temp_string.center(7)}'
                    formatted_my_player_mmr_change = await self.pos_mmr_wrapper(string_my_player_mmr_change)
                else:
                    string_my_player_mmr_change = f'{str(my_player_mmr_change).center(7)}'
                    formatted_my_player_mmr_change = await self.neg_mmr_wrapper(string_my_player_mmr_change)
                mmr_table_string += f'{formatted_my_player_mmr_change}|'

                # Check for new peak
                string_my_player_new_mmr = str(my_player_new_mmr).center(9)
                # print(f'current peak: {my_player_peak} | new mmr value: {my_player_new_mmr}')
                if my_player_peak < (my_player_new_mmr):
                    formatted_my_player_new_mmr = await self.peak_mmr_wrapper(string_my_player_new_mmr)
                    with DBA.DBAccess() as db:
                        db.execute('UPDATE player SET peak_mmr = %s WHERE player_id = %s;', (my_player_new_mmr, player[0]))
                else:
                    formatted_my_player_new_mmr = string_my_player_new_mmr
                mmr_table_string += f'{formatted_my_player_new_mmr}|'

                # Send updates to DB
                try:
                    with DBA.DBAccess() as db:
                        # Get ID of the last inserted table
                        temp = db.query('SELECT mogi_id FROM mogi WHERE tier_id = %s ORDER BY create_date DESC LIMIT 1;', (SQ_TIER_ID,))
                        db_mogi_id = temp[0][0]
                        # Insert reference record
                        db.execute('INSERT INTO player_mogi (player_id, mogi_id, place, score, prev_mmr, mmr_change, new_mmr) VALUES (%s, %s, %s, %s, %s, %s, %s);', (player[0], db_mogi_id, int(my_player_place), int(my_player_score), int(my_player_mmr), int(my_player_mmr_change), int(my_player_new_mmr)))
                        # Update player record
                        db.execute('UPDATE player SET mmr = %s WHERE player_id = %s;', (my_player_new_mmr, player[0]))
                        # Remove player from lineups
                        # db.execute('DELETE FROM lineups WHERE player_id = %s AND tier_id = %s;', (player[0], SQ_TIER_ID)) # YOU MUST SUBMIT TABLE IN THE TIER THE MATCH WAS PLAYED
                        # # Clear sub leaver table
                        # db.execute('DELETE FROM sub_leaver WHERE tier_id = %s;', (SQ_TIER_ID,))
                except Exception as e:
                    # print(e)
                    await self.send_to_debug_channel(ctx, f'FATAL TABLE ERROR: {e}')
                    pass

                # Check for rank changes
                with DBA.DBAccess() as db:
                    db_ranks_table = db.query('SELECT rank_id, mmr_min, mmr_max FROM ranks WHERE rank_id > %s;', (1,))
                for i in range(len(db_ranks_table)):
                    rank_id = db_ranks_table[i][0]
                    min_mmr = db_ranks_table[i][1]
                    max_mmr = db_ranks_table[i][2]
                    # Rank up - assign roles - update DB
                    try:
                        if my_player_mmr < min_mmr and my_player_new_mmr >= min_mmr:
                            guild = self.bot.get_guild(Lounge[0])
                            current_role = guild.get_role(my_player_rank_id)
                            new_role = guild.get_role(rank_id)
                            member = await guild.fetch_member(player[0])
                            await member.remove_roles(current_role)
                            await member.add_roles(new_role)
                            await results_channel.send(f'{my_player_name} has been promoted to {new_role}')
                            with DBA.DBAccess() as db:
                                db.execute('UPDATE player SET rank_id = %s WHERE player_id = %s;', (rank_id, player[0]))
                            my_player_new_rank += f'+ {new_role}'
                        # Rank down - assign roles - update DB
                        elif my_player_mmr > max_mmr and my_player_new_mmr <= max_mmr:
                            guild = self.bot.get_guild(Lounge[0])
                            current_role = guild.get_role(my_player_rank_id)
                            new_role = guild.get_role(rank_id)
                            member = await guild.fetch_member(player[0])
                            await member.remove_roles(current_role)
                            await member.add_roles(new_role)
                            await results_channel.send(f'{my_player_name} has been demoted to {new_role}')
                            with DBA.DBAccess() as db:
                                db.execute('UPDATE player SET rank_id = %s WHERE player_id = %s;', (rank_id, player[0]))
                            my_player_new_rank += f'- {new_role}'
                    except Exception as e:
                        # print(e)
                        pass
                        # my_player_rank_id = role_id
                        # guild.get_role(role_id)
                        # guild.get_member(discord_id)
                        # member.add_roles(discord.Role)
                        # member.remove_roles(discord.Role)
                string_my_player_new_rank = f'{str(my_player_new_rank).center(12)}'
                formatted_my_player_new_rank = await self.new_rank_wrapper(string_my_player_new_rank, my_player_new_mmr)
                mmr_table_string += f'{formatted_my_player_new_rank}'
                string_my_player_place = ''

        # Create imagemagick image
        # print('_______')
        # print(mmr_table_string)
        # print('_______')
        # https://imagemagick.org/script/color.php
        pango_string = f'pango:<tt>{mmr_table_string}</tt>'
        mmr_filename = f'/home/sq/squad_queue_v2/images/{hex(ctx.author.id)}mmr.jpg'
        # correct = subprocess.run(['convert', '-background', 'gray21', '-fill', 'white', pango_string, mmr_filename], check=True, text=True)
        correct = subprocess.run(['convert', '-background', 'None', '-fill', 'white', pango_string, 'mkbg.jpg', '-compose', 'DstOver', '-layers', 'flatten', mmr_filename], check=True, text=True)
        # '+swap', '-compose', 'Over', '-composite', '-quality', '100',
        # '-fill', '#00000040', '-draw', 'rectangle 0,0 570,368',
        f=discord.File(mmr_filename, filename='mmr.jpg')
        sf=discord.File(f'/home/sq/squad_queue_v2/images/{hex(ctx.author.id)}table.png', filename='table.jpg')

        # Create embed
        results_channel = self.bot.get_channel(db_results_channel)
        embed2 = discord.Embed(title=f'Tier {tier_name.upper()} Results', color = discord.Color.blurple())
        embed2.add_field(name='Table ID', value=f'{str(db_mogi_id)}', inline=True)
        embed2.add_field(name='Tier', value=f'{tier_name.upper()}', inline=True)
        embed2.add_field(name='Submitted by', value=f'<@{ctx.author.id}>', inline=True)
        embed2.add_field(name='View on website', value=f'https://200-lounge.com/mogi/{db_mogi_id}', inline=False)
        embed2.set_image(url='attachment://table.jpg')
        table_message = await results_channel.send(content=None, embed=embed2, file=sf)
        table_url = table_message.embeds[0].image.url
        try:
            with DBA.DBAccess() as db:
                db.query('UPDATE mogi SET table_url = %s WHERE mogi_id = %s;', (table_url, db_mogi_id))
        except Exception as e:
            await self.send_to_debug_channel(ctx, f'Unable to get table url: {e}')
            pass

        embed = discord.Embed(title=f'Tier {tier_name.upper()} MMR', color = discord.Color.blurple())
        embed.add_field(name='Table ID', value=f'{str(db_mogi_id)}', inline=True)
        embed.add_field(name='Tier', value=f'{tier_name.upper()}', inline=True)
        embed.add_field(name='Submitted by', value=f'<@{ctx.author.id}>', inline=True)
        embed.add_field(name='View on website', value=f'https://200-lounge.com/mogi/{db_mogi_id}', inline=False)
        embed.set_image(url='attachment://mmr.jpg')
        await results_channel.send(content=None, embed=embed, file=f)
        #  discord ansi coloring (doesn't work on mobile)
        # https://gist.github.com/kkrypt0nn/a02506f3712ff2d1c8ca7c9e0aed7c06
        # https://rebane2001.com/discord-colored-text-generator/ 
        await self.queue_or_send(ctx, '`Table Accepted.`')


    async def check_if_banned_characters(self, message):
        for value in secretly.BANNED_CHARACTERS:
            if value in message:
                return True
        return False
    async def check_for_dupes_in_list(self, my_list):
        if len(my_list) == len(set(my_list)):
            return False
        else:
            return True

    async def send_to_debug_channel(self, ctx, error):
        channel = self.bot.get_channel(secretly.debug_channel)
        embed = discord.Embed(title='Error', description='>.<', color = discord.Color.blurple())
        embed.add_field(name='Issuer: ', value=ctx.author.mention, inline=False)
        embed.add_field(name='Error: ', value=str(error), inline=False)
        embed.add_field(name='Discord ID: ', value=ctx.author.id, inline=False)
        await channel.send(content=None, embed=embed)
    
    async def new_rank_wrapper(self, input, mmr):
        # print(f'input: {input}')
        # print(f'mmr: {mmr}')
        if input:
            if mmr < 1500:
                return await self.iron_wrapper(input)
            elif mmr >= 1500 and mmr < 3000:
                return await self.bronze_wrapper(input)
            elif mmr >= 3000 and mmr < 4500:
                return await self.silver_wrapper(input)
            elif mmr >= 4500 and mmr < 6000:
                return await self.gold_wrapper(input)
            elif mmr >= 6000 and mmr < 7500:
                return await self.platinum_wrapper(input)
            elif mmr >= 7500 and mmr < 9000:
                return await self.diamond_wrapper(input)
            elif mmr >= 9000 and mmr < 11000:
                return await self.master_wrapper(input)
            elif mmr >= 11000:
                return await self.grandmaster_wrapper(input)
            else:
                return input
        else:
            return input

    async def grandmaster_wrapper(self, input):
        # return (f'[0;2m[0;40m[0;31m{input}[0m[0;40m[0m[0m')
        return (f'<span foreground="DarkRed">{input}</span>')

    async def master_wrapper(self, input):
        # return (f'[2;40m[2;37m{input}[0m[2;40m[0m')
        return (f'<span foreground="black">{input}</span>')

    async def diamond_wrapper(self, input):
        # return (f'[0;2m[0;34m{input}[0m[0m')
        return (f'<span foreground="PowderBlue">{input}</span>')

    async def platinum_wrapper(self, input):
        # return (f'[2;40m[2;36m{input}[0m[2;40m[0m')
        return (f'<span foreground="teal">{input}</span>')

    async def gold_wrapper(self, input):
        # return (f'[2;40m[2;33m{input}[0m[2;40m[0m')
        return (f'<span foreground="gold1">{input}</span>')

    async def silver_wrapper(self, input):
        # return (f'[0;2m[0;42m[0;37m{input}[0m[0;42m[0m[0m')
        return (f'<span foreground="LightBlue4">{input}</span>')

    async def bronze_wrapper(self, input):
        # return (f'[0;2m[0;47m[0;33m{input}[0m[0;47m[0m[0m')
        return (f'<span foreground="DarkOrange2">{input}</span>')

    async def iron_wrapper(self, input):
        # return (f'[0;2m[0;30m[0;47m{input}[0m[0;30m[0m[0m')
        return (f'<span foreground="DarkGray">{input}</span>')

    async def pos_mmr_wrapper(self, input):
        # return (f'[0;2m[0;32m{input}[0m[0m')
        return (f'<span foreground="chartreuse">{input}</span>')

    async def neg_mmr_wrapper(self, input):
        # return (f'[0;2m[0;31m{input}[0m[0m')
        return (f'<span foreground="Red2">{input}</span>')

    async def peak_mmr_wrapper(self, input):
        # return (f'[0;2m[0;41m[0;37m{input}[0m[0;41m[0m[0m')
        return (f'<span foreground="Yellow1"><i>{input}</i></span>')
          

def setup(bot):
    bot.add_cog(Mogi(bot))
