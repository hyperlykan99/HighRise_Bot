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

### Sync (all players)
- [ ] Normal player: `!sync @user` works
- [ ] Normal player: `!syncstop` works
- [ ] Normal player: `!syncstatus` works
- [ ] Normal player: `!sync all` is blocked (❌ Only owner/admin)
- [ ] Normal player: `!sync all @user` is blocked
- [ ] Normal player: `!synchelp` does NOT show `!sync all`
- [ ] Sync follows target's custom loops
- [ ] Sync follows target's bot emotes
- [ ] Sync follows target's dancefloor moves
- [ ] Movement stops custom loop — does NOT stop sync itself

### Hearts (all players)
- [ ] Normal player: `!heart @user` (1 heart) works

### Help commands (all players)
- [ ] `!emotehelp` visible to all
- [ ] `!customhelp` visible to all (no VIP message)
- [ ] `!synchelp` visible to all
- [ ] `!socialhelp` visible to all
- [ ] `!dancefloorhelp` — blocked for non-staff (❌ Dancefloor commands are for staff only.)
- [ ] `!botemotehelp` — blocked for non-staff (❌ Bot emote commands are for staff only.)

---

## VIP+ required

- [ ] VIP: `!sync @user` works
- [ ] VIP: `!sync all` is blocked
- [ ] VIP: `!sync all @user` is blocked
- [ ] VIP: `!synchelp` does NOT show `!sync all`
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
- [ ] `!botemotehelp` shows full help
- [ ] `!botemote @bot <emote>`
- [ ] `!botemote stop @bot`
- [ ] `!botemotes`
- [ ] Bot emotes persist after restart

### Dancefloor (staff)
- [ ] `!dancefloorhelp` shows full help (not blocked)
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

### Owner/Admin sync-all
- [ ] `!sync all` works (all non-bot players synced to sender)
- [ ] `!sync all @user` works (all non-bot players synced to @user)
- [ ] Bots are skipped as followers
- [ ] Bot as `@user` target is rejected (❌ Bots cannot be sync leaders.)
- [ ] Leader is not added as their own follower
- [ ] `!synchelp` shows `!sync all` and `!sync all @user`
- [ ] `!synchelp` shows `!syncpersist on|off`
- [ ] Followers added by `!sync all` follow normal emotes
- [ ] Followers added by `!sync all` follow custom loops
- [ ] Followers added by `!sync all` follow dancefloor emotes
- [ ] `!syncstop` still removes a user from sync after `!sync all`
- [ ] Sync persistence still works if enabled

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
- [ ] `!dancefloorhelp` — blocked for non-staff
- [ ] `!botemote` — blocked for non-staff
- [ ] `!botemotehelp` — blocked for non-staff
- [ ] `!syncpersist` — blocked for non-staff
- [ ] `!sync all` — blocked for non-admin
- [ ] `!sync all @user` — blocked for non-admin
- [ ] `!heart all` — blocked for non-staff
- [ ] `!superpunch`, `!yeet`, `!duel` — blocked for non-staff

---

## Regression tests

- [ ] Sync followers still follow normal emotes after any of the above changes
- [ ] Sync followers still follow custom loops
- [ ] Sync followers still follow dancefloor emotes
- [ ] `!syncstop` still removes a user from sync
- [ ] Sync persistence still works if enabled
- [ ] Dancefloor catch-up sends ONE immediate emote — does NOT start a loop that locks followers on the first emote
