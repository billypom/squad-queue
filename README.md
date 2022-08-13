# QBot

This is a fork of Cynda's [QueueBot](https://github.com/cyndaquilx/QueueBot), adjusted for 200cc Lounge. See his documentation for setup.

Report bugs and errors to peng2n#2222 on Discord
________________________________________________

# Player Commands

`!c` option: @partner @partner2 | can up with your partner(s) / accept invitation from squad leader

`!d` | drop 

`!list` | list 

`!finish` | Finishes the room by adding a checkmark to the channel. Anyone in the room can call this command

`!squad` | Info about your squad

`!view_schedule` | Displays the schedule

# Admin Commands

`!start` [int] size | Starts mogi in current channel of [int] size (2, 3, 4)

`!end` | Ends the mogi

`!close` | Closes the mogi. Players cannot join or drop

`!open` | Opens to mogi. Players can join and drop

`!r` [int] id | Removes the given squad ID from the mogi list

`!remove_event` [int] id | Removes an event from the schedule

`!makeRooms` | NOT DOCUMENTED | Makes rooms probably 

`!remakeRooms` | NOT DOCUMENTED | Looks like it deletes the rooms and makes new rooms 

`!sortTeams` | Backup command if !makerooms doesn't work; doesn't make channels, just sorts teams in MMR order

`!schedule` [int] size [str] time | Schedule an event in the future, so staff doesn't have to be online to open mogi & make rooms.

`!pt` [str] time | Returns a unix timestamp, wrapped with discord's formatting. Use this string in the #sq-info channel to display scheduled squad queue events in a user's local timezone 

`!lockerdown` | Locks down the #sq-join channel

example: `#1000` **2v2:** <t:1746550800:F>
