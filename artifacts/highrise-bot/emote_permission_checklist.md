# Emote Permission Checklist

## Everyone / Public (no role required)

### Basic emotes
- [ ] Plain emote name plays emote
- [ ] `!emotes` lists all emotes
- [ ] `!emoteinfo <name>` shows details
- [ ] `!loopemote <name>` loops an emote

### Custom loops (all players)
- [ ] Normal player can use `!customemote e1 e2 e3`
- [ ] Normal player can use `!customtimed e1 5 e2 10`
- [ ] Normal player can use `!stopcustom`
- [ ] Normal player can use `!savecustom <pack> e1 e2`
- [ ] Normal player can use `!savecustomtimed <pack> e1 5 e2 10`
- [ ] Normal player can use `!playcustom <pack>`
- [ ] Normal player can use `!custompacks`
- [ ] Normal player can use `!custominfo <pack>`
- [ ] Normal player can use `!renamecustom <old> <new>`
- [ ] Normal player can use `!deletecustom <pack>`
- [ ] Normal player sees all custom commands in `!customhelp` (no VIP message)
- [ ] Custom loop persists after bot restart
- [ ] Custom timed loop persists after bot restart
- [ ] Saved custom packs persist after bot restart

### Sync
- [ ] Normal player can use `!sync @user`
- [ ] Normal player can use `!syncstop`
- [ ] Normal player can use `!syncstatus`
- [ ] Sync follows target's custom loops
- [ ] Sync follows target's bot emotes
- [ ] Sync follows target's dancefloor moves
- [ ] Movement stops custom loop (does NOT stop sync itself)

### Hearts
- [ ] Normal player can use `!heart @user` (1 heart)

### Help commands (all players)
- [ ] `!emotehelp`
- [ ] `!customhelp`
- [ ] `!synchelp`
- [ ] `!socialhelp`

---

## VIP+ required

- [ ] `!kiss @user`
- [ ] `!slap @user`
- [ ] `!bonk @user`
- [ ] `!hypnotize @user`
- [ ] `!kicksocial @user`
- [ ] `!heart @user <N>` (burst, N > 1)

---

## Staff / Manager / Owner only

### Emote admin
- [ ] `!setemote <name> time <sec>`
- [ ] `!syncpersist on|off`

### Dancefloor (staff)
- [ ] `!dancefloor setpoint 1`
- [ ] `!dancefloor setpoint 2`
- [ ] `!dancefloor save`
- [ ] `!dancefloor emotes <a b c>`
- [ ] `!dancefloor timed <a sec b sec>`
- [ ] `!dancefloor random <N>`
- [ ] `!dancefloor randomtimed <N|all> <sec>`
- [ ] `!dancefloor randomtimed <N|all> <min> <max>`
- [ ] `!dancefloor start`
- [ ] `!dancefloor stop`
- [ ] `!dancefloor status`
- [ ] `!dancefloor debug`
- [ ] `!dancefloor clear`
- [ ] `!dancefloor savepack <name>`
- [ ] `!dancefloor loadpack <name>`
- [ ] `!dancefloor packs`
- [ ] `!dancefloor packinfo <name>`
- [ ] `!dancefloor renamepack <old> <new>`
- [ ] `!dancefloor deletepack <name>`

### Bot emotes (staff)
- [ ] `!botemote @bot <emote>`
- [ ] `!botemote stop @bot`
- [ ] `!botemotes`
- [ ] Bot emotes persist after restart

### Staff socials
- [ ] `!superpunch @user`
- [ ] `!yeet @user`
- [ ] `!duel @user`
- [ ] `!heart all`
- [ ] `!hearts all <N>`
- [ ] `!heart @user 100`

---

## Normal player must NOT be able to

- [ ] `!setemote` — blocked for non-staff
- [ ] `!dancefloor` any subcommand — blocked for non-staff
- [ ] `!botemote` — blocked for non-staff
- [ ] `!syncpersist` — blocked for non-staff
- [ ] `!heart all` — blocked for non-staff
- [ ] `!superpunch`, `!yeet`, `!duel` — blocked for non-staff
