# every lounge is different so this file will probably
# have to be completely rewritten for each server.
# my implementation is here as an example; gspread is only
# needed if you get MMR from a spreadsheet.

# The important part is that the function returns False
# if a player's MMR can't be found,
# and returns the player's MMR otherwise

import discord
from discord.ext import commands
import DBA

# import gspread
# gc = gspread.service_account(filename='credentials.json')

#opens a lookup worksheet so MMR is retrieved quickly
# sh = gc.open_by_key('1LOfhuzGsEdMuqAmtb6n-dNiGd7S9kx28VHwBcU2K7nE')
# mmrs = sh.worksheet("search")

class Sheet(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
    #async def mmr(self, member: discord.Member):
    async def mmr(self, members):
        print(members)
        try:
            print('try')
            temp_name = members[0]
            for member in members:
                with DBA.DBAccess() as db:
                    check_values = db.query('SELECT mmr FROM player WHERE player_name = %s;', (member,))
        except Exception:
            print('except')
            with DBA.DBAccess() as db:
                check_values = db.query('SELECT mmr FROM player WHERE player_name = %s;', (members,))
        # mmrs.update('B3:B%d' % int(2+len(members)), [[member] for member in members])
        # check_values = mmrs.get('C3:C%d' % int(2+len(members)))
        return_mmrs = []
        for mmr in check_values:
            if mmr[0] is None:
                return_mmrs.append(False)
                continue
            # if mmr[0] == "Placement":
            #     return_mmrs.append(2000)
            #     continue
            # if mmr[0] == "N":
            #     return_mmrs.append(False)
            #     continue
            return_mmrs.append(int(mmr[0]))
        print(return_mmrs)
        return return_mmrs

def setup(bot):
    bot.add_cog(Sheet(bot))
