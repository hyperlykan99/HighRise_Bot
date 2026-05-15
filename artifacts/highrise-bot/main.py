"""
main.py
-------
HangoutBot — all-in-one Highrise Mini Game Bot.

This is the entry point for the current single-bot setup.
When you're ready to split into separate bots, create a new entry point
file for each mode (e.g. game_bot.py, dj_bot.py, blackjack_bot.py) and
import from the shared root modules:

    from economy import handle_balance, handle_daily, handle_leaderboard
    from games   import handle_game_command, handle_answer
    from admin   import handle_admin_command
    import database as db
    import config

All bots share the same highrise_hangout.db database, so player coins,
stats, and daily rewards carry over automatically.

─────────────────────────────────────────────────────────────────────────────
Future bot layout (example):
─────────────────────────────────────────────────────────────────────────────
  game_bot.py         ← imports economy, games, admin
  dj_bot.py           ← imports economy, modules/dj.py, admin
  blackjack_bot.py    ← imports economy, modules/blackjack.py, admin
  host_bot.py         ← imports economy, admin, custom host logic
─────────────────────────────────────────────────────────────────────────────
"""

import asyncio
import modules.bot_state as bot_state
from highrise import BaseBot, User
from highrise.__main__ import BotDefinition, main as highrise_main

import database as db
import config

# Shared root-level modules (reusable by any future bot)
from economy import (
    handle_balance, handle_daily, handle_leaderboard,
    handle_profile, handle_level, handle_xp_leaderboard,
    handle_streak,
    handle_dailystatus,
)
from games        import handle_game_command, handle_answer as games_handle_answer
from admin        import handle_admin_command
from modules.admin_cmds import (
    handle_setcoins, handle_editcoins, handle_resetcoins,
    handle_addeventcoins, handle_removeeventcoins,
    handle_seteventcoins, handle_editeventcoins, handle_reseteventcoins,
    handle_addxp, handle_removexp, handle_setxp, handle_editxp, handle_resetxp,
    handle_setlevel, handle_editlevel, handle_addlevel,
    handle_removelevel, handle_promotelevel, handle_demotelevel,
    handle_setrep, handle_editrep, handle_resetrep,
    handle_givetitle, handle_removetitle, handle_settitle, handle_cleartitle,
    handle_givebadge, handle_removebadge, handle_removebadgefrom,
    handle_setbadge, handle_clearbadge,
    handle_addvip, handle_removevip, handle_vipstatus, handle_vips,
    handle_setvipprice,
    handle_resetbjstats, handle_resetrbjstats,
    handle_resetpokerstats, handle_resetcasinostats,
    handle_adminpanel, handle_adminlogs, handle_adminloginfo, handle_checkhelp,
    handle_mycommands, handle_helpsearch,
    handle_stability,
)
from modules.vip import (
    handle_vip, handle_vipperks, handle_myvip, handle_giftvip,
    handle_viplist, handle_grantvip, handle_buyvip,
    handle_donate, handle_donationgoal, handle_topdonors,
    handle_donationdebug,
    handle_sponsor, handle_sponsorgoldrain, handle_sponsorevent,
    handle_supporter, handle_perks,
    handle_setdonationgoal, handle_donationaudit, handle_setsponsorprice,
)
from modules.luxe import (
    handle_tickets, handle_luxeshop, handle_buyluxe,
    handle_buyticket, handle_buycoins,
    handle_use as handle_use_permit,
    handle_autotime, handle_luxeadmin, handle_vipadmin,
    handle_autoconvert, handle_economydefaults,
)
from modules.shop         import (
    handle_shop, handle_buy, handle_equip, handle_myitems,
    handle_badgeinfo, handle_titleinfo,
)
from modules.badge_market import (
    handle_shop_badges,
    handle_badgeinfo    as handle_badgeinfo_emoji,
    handle_buy_badge, handle_equip_badge, handle_unequip_badge,
    handle_mybadges, handle_badges_view,
    handle_badgemarket, handle_badgelist, handle_badgebuy,
    handle_badgecancel, handle_mybadgelistings, handle_badgeprices,
    handle_addbadge, handle_editbadgeprice, handle_setbadgeflag,
    handle_givebadge_emoji, handle_removebadge_from as handle_removebadge_emoji,
    handle_giveemojibadge, handle_badgecatalog, handle_badgeadmin,
    handle_setbadgemarketfee, handle_badgemarketlogs,
    handle_buybadge_cmd, handle_staffbadge,
    handle_emojitest, handle_disableemoji, handle_enableemoji,
    handle_badges_cmd_router, handle_badge_help,
    handle_marketsearch, handle_marketfilter,
    handle_marketaudit, handle_marketdebug,
    handle_forcelistingcancel, handle_clearbadgelocks,
    handle_market_help, handle_trade_help,
    handle_trade_start, handle_tradeadd, handle_tradecoins,
    handle_tradeview, handle_tradeconfirm, handle_tradecancel,
)
from modules.numbered_shop import (
    handle_buy_number,
    handle_confirmbuy,
    handle_cancelbuy,
    handle_shop_nav,
    handle_badgemarket_nav,
    handle_eventshop_nav,
    handle_shopadmin,
    handle_shoptest,
    handle_setshopconfirm,
    handle_seteventconfirm,
)
from modules.achievements import handle_achievements, handle_claim_achievements
from modules.blackjack           import (
    handle_bj, handle_bj_set, handle_bj_shoe, handle_bj_shoe_reset,
    handle_bj_cards, handle_bj_rules, handle_bj_bonus_settings,
    reset_table as bj_reset_table,
    soft_reset_table as bj_soft_reset_table,
    startup_bj_recovery,
)
from modules.realistic_blackjack import (
    handle_rbj, handle_rbj_set,
    handle_bet,
    handle_hit, handle_stand, handle_double, handle_split,
    handle_insurance, handle_surrender,
    handle_bjstatus, handle_bjforce, handle_bjtest, handle_bjadmin,
    reset_table as rbj_reset_table,
    soft_reset_table as rbj_soft_reset_table,
    startup_rbj_recovery,
)
from modules.poker import (
    POKER_HELP_PAGES,
    handle_poker, handle_pokerhelp, handle_pokerstatus,
    handle_pokerstats, handle_pokerlb,
    handle_setpokerbuyin, handle_setpokerplayers,
    handle_setpokerlobbytimer, handle_setpokertimer,
    handle_setpokerraise,
    handle_setpokerdailywinlimit, handle_setpokerdailylosslimit,
    handle_resetpokerlimits,
    handle_setpokerturntimer, handle_setpokerlimits,
    handle_setpokerblinds, handle_setpokerante,
    handle_setpokernexthandtimer, handle_setpokermaxstack,
    handle_setpokeridlestrikes,
    handle_pokerdebug, handle_pokerfix, handle_pokerrefundall,
    handle_pokercleanup, handle_confirmclosepoker,
    get_poker_recovery_recommendation,
    startup_poker_recovery,
    soft_reset_table as poker_soft_reset_table,
    reset_table as poker_reset_table,
    get_poker_state_str,
    handle_pokermode, handle_pokerpace, handle_setpokerpace,
    handle_pokerstacks, handle_setpokerstack, handle_dealstatus,
    handle_pokerplayers,
    handle_pokerdashboard,
    handle_pokerpause, handle_pokerresume,
    handle_pokerforceadvance, handle_pokerforceresend,
    handle_pokerturn, handle_pokerpots, handle_pokeractions,
    handle_pokerstylepreview,
    handle_pokerresetturn, handle_pokerresethand, handle_pokerresettable,
    handle_poker_user_left,
)
from modules.poker_v2 import handle_poker_v2
from modules.casino_settings     import (
    handle_casinosettings, handle_casinolimits, handle_casinotoggles,
    handle_setbjlimits, handle_setrbjlimits,
)
from modules.subscribers         import (
    handle_subscribe, handle_unsubscribe, handle_substatus,
    handle_subhelp,
    handle_subscribers, handle_dmnotify, handle_announce_subs,
    handle_announce_vip, handle_announce_staff,
    handle_debugsub,
    handle_forcesub, handle_fixsub,
    process_incoming_dm,
    deliver_pending_subscriber_messages,
)
from modules.daily_admin import handle_dailyadmin
from modules.notifications import (
    send_notification,
    deliver_pending_notifications,
    handle_notifysettings, handle_notify, handle_notifyhelp,
    handle_notifications, handle_clearnotifications,
    handle_notifystats, handle_notifyprefs,
    handle_notifyuser, handle_broadcasttest,
    handle_debugnotify, handle_testnotify,
    handle_testnotifyall, handle_pendingnotify, handle_clearpendingnotify,
)
from modules.maintenance         import (
    handle_botstatus, handle_dbstats,
    handle_maintenance, handle_reloadsettings, handle_cleanup,
    handle_healthcheck,
    handle_restarthelp, handle_restartstatus,
    handle_softrestart,
    handle_restartbot,
    is_maintenance, is_bot_maintenance,
)
from modules.notif_debug import (
    handle_notifydebug, handle_roomusers,
    handle_testwhisper, handle_notifrefresh,
)
from modules.qol_cmds import (
    handle_quicktest, handle_playercheck, handle_claimrewards,
    handle_eventcalendar, handle_lastupdate,
    handle_knownissues, handle_knownissue,
    handle_feedback, handle_feedbacks,
    handle_todo, handle_aetest, handle_ownercheck,
    handle_botstatus as handle_botstatus_simple,
)
from modules.beta import (
    handle_betamode, handle_betacheck, handle_betadash,
    handle_staffdash, handle_stafftools,
    handle_testmenu, handle_betahelp,
    handle_quickstart, handle_launchready,
    handle_issueadmin, handle_bugs_admin, handle_errors_admin,
    handle_announce_room, handle_announceadmin,
    is_beta_mode, maybe_send_beta_notice,
    rotating_announcement_loop,
)
from modules.beta_review import (
    handle_betatest, handle_topissues, handle_balanceaudit,
    handle_livebalance, handle_luxebalance, handle_retentionreview,
    handle_eventreview, handle_seasonreview, handle_funnel,
    handle_betareport, handle_launchblockers, handle_betastaff,
    handle_feedbacks_review,
)
from modules.release import (
    handle_rcmode, handle_production, handle_featurefreeze,
    handle_economylock, handle_registrylock,
    handle_releasenotes, handle_version,
    handle_backup, handle_rollbackplan, handle_restorebackup,
    handle_ownerchecklist, handle_launchannounce,
    handle_whatsnew, handle_knownissues,
    handle_releasedash, handle_finalaudit,
    handle_qastatus, handle_ownerqa, handle_ownertest,
    handle_stablecheck, handle_hotfixpolicy, handle_stablelock,
)
from modules.monitor import (
    handle_launchmode, handle_postlaunch, handle_livehealth,
    handle_bugdash, handle_feedbackdash, handle_dailyreview,
    handle_economymonitor, handle_luxemonitor, handle_retentionmonitor,
    handle_eventmonitor, handle_casinomonitor, handle_bjmonitor,
    handle_pokermonitor, handle_errordash,
    handle_hotfix, handle_hotfixlog, handle_launchlocks, handle_snapshot,
)
from modules.permissions         import (
    is_owner, is_admin, is_manager, is_moderator,
    can_moderate, can_manage_games, can_manage_economy, can_audit,
)
from modules.profile import (
    handle_profile_cmd,
    handle_stats_cmd,
    handle_badges_cmd,
    handle_casino_profile,
    handle_privacy,
    handle_profileadmin,
    handle_profileprivacy,
    handle_resetprofileprivacy,
    handle_flex,
    handle_profile_settings,
    handle_profile_help,
)
from modules.onboarding import (
    on_join_welcome,
    check_tutorial_step,
    handle_start       as handle_onboarding_start,
    handle_guide       as handle_onboarding_guide,
    handle_newbie      as handle_onboarding_newbie,
    handle_tutorial    as handle_onboarding_tutorial,
    handle_starter     as handle_onboarding_starter,
    handle_onboardadmin,
)
from modules.dashboard import handle_wallet, handle_casino_dash, handle_dashboard
from modules.audit import (
    handle_audithelp, handle_audit,
    handle_auditbank, handle_auditcasino, handle_auditeconomy,
)
from modules.cmd_audit import (
    handle_checkcommands as _audit_checkcommands,
    handle_checkhelp_audit as _audit_checkhelp,
    handle_missingcommands, handle_routecheck,
    handle_silentcheck, handle_commandtest,
    handle_fixcommands, handle_testcommands,
    handle_commandintegrity, handle_commandrepair,
    handle_commandaudit, handle_commandissues,
    handle_commandtestall, handle_commandtestgroup,
    handle_auditlog,
)
from modules.sys_dashboard import handle_sys_dashboard
from modules.reward_center import (
    handle_rewardpending, handle_rewardlogs,
    handle_markrewardpaid, handle_economyreport,
)
from modules.weekly_lb import (
    handle_weeklylb, handle_weeklyreset, handle_weeklyrewards,
    handle_setweeklyreward, handle_weeklystatus,
)
from modules.mining_weights import (
    handle_oreweightlb, handle_myheaviest, handle_oreweights, handle_topweights,
    handle_setweightlbmode,
    handle_mineannounce, handle_setmineannounce, handle_setoreannounce,
    handle_oreannounce, handle_mineannouncesettings,
    handle_mineweights, handle_setmineweights, handle_setweightscale,
    handle_setrarityweightrange, handle_oreweightsettings,
)
from modules.economy_settings import (
    handle_economysettings,
    handle_setdailycoins, handle_setgamereward,
    handle_setmaxbalance, handle_settransferfee,
)
from modules.quests import (
    handle_questhelp, handle_quests,
    handle_dailyquests, handle_weeklyquests, handle_claimquest,
)
from modules.missions import (
    handle_missions,
    handle_weekly       as handle_weekly_missions,
    handle_claimmission,
    handle_claimdaily   as handle_claimdaily_missions,
    handle_claimweekly,
    handle_milestones,
    handle_claimmilestone,
    handle_level        as handle_mission_level,
    handle_season,
    handle_topseason,
    handle_seasonrewards,
    handle_today,
    handle_missionadmin,
    handle_retentionadmin,
)
from modules.events import (
    handle_event, handle_events, handle_eventhelp, handle_eventstatus,
    handle_nextevent, handle_eventloop,
    handle_startevent, handle_stopevent,
    handle_eventadmin,
    handle_eventpoints, handle_eventshop, handle_buyevent,
    startup_event_check,
    handle_adminsblessing, handle_eventresume,
    handle_autogamestatus, handle_autogameresume,
    # B-project: new mining event handlers
    handle_mineevents, handle_mineboosts, handle_luckstatus,
    handle_miningblessing, handle_luckevent, handle_miningevent_start,
    handle_eventmanager, handle_eventpanel, handle_eventeffects,
    handle_autoeventstatus, handle_autoeventadd,
    handle_autoeventremove, handle_autoeventinterval,
    handle_eventlist, handle_eventpreview,
    handle_aepool, handle_aeadd, handle_aeremove,
    handle_aequeue, handle_aenext, handle_eventheartbeat,
    handle_eventcooldowns, handle_seteventcooldown,
    handle_eventweights, handle_seteventweight, handle_eventhistory,
    startup_mining_event_check,
    handle_eventpreset,
    handle_setaeinterval, handle_setaeduration,
    handle_aeinterval, handle_aeduration,
    handle_aererollnext, handle_setnextae, handle_aehistory,
    handle_aeskip, handle_aeskipnext,
)
from modules.reports import (
    handle_report, handle_bug, handle_myreports,
    handle_reports, handle_reportinfo, handle_closereport, handle_reportwatch,
)
from modules.moderation import (
    handle_mute, handle_unmute, handle_mutes,
    handle_mutestatus, handle_forceunmute,
    handle_warn, handle_warnings, handle_clearwarnings,
    handle_rules, handle_setrules, handle_automod,
)
from modules.automod import automod_check
from modules.safety import (
    handle_softban, handle_unsoftban,
    handle_safetyadmin, handle_economysafety, handle_safetydash,
    handle_modhelp as handle_modhelp_tiered,
    is_softbanned, log_safety_alert,
    is_event_processed, mark_event_processed,
    log_economy_tx,
)
from modules.help_cmds import (
    handle_commands as handle_commands_browser,
    handle_command_detail,
    handle_currencycheck,
)
from modules.staff_cmds import (
    handle_staffnote, handle_staffnotes,
    handle_permissioncheck, handle_rolecheck,
)
from modules.analytics import (
    handle_ownerdash,
    handle_playerstats,
    handle_economydash,
    handle_luxedash,
    handle_conversiondash,
    handle_minedash,
    handle_fishdash,
    handle_activitydash,
    handle_hourlyaudit,
    handle_shopdash,
    handle_vipdash,
    handle_retentiondash,
    handle_eventdash,
    handle_seasondash,
    handle_analyticsdash,
)
from modules.reputation import (
    handle_rep, handle_reputation, handle_toprep,
    handle_replog, handle_addrep, handle_removerep,
)
from modules.bank import (
    handle_bank, handle_send, handle_transactions, handle_bankstats,
    handle_banknotify,
    handle_viewtx, handle_bankwatch, handle_bankblock, handle_banksettings,
    handle_bank_set, handle_ledger,
    handle_notifications as handle_bank_notifications,
    handle_clearnotifications as handle_bank_clearnotifications,
    handle_delivernotifications, handle_pendingnotifications,
)
from modules.tips import (
    process_tip_event,
    handle_tiprate, handle_tipstats, handle_tipleaderboard,
    handle_settiprate, handle_settipcap, handle_settiptier,
    handle_settipautosub, handle_settipresubscribe,
    handle_debugtips,
    record_debug_any_event,
    extract_gold_from_tip,
    _SDK_VERSION as _TIP_SDK_VERSION,
)
from modules.auto_games import (
    start_auto_game_loop, start_auto_event_loop, start_activity_prompt_loop,
    handle_autogames, handle_autoevents,
    handle_setgametimer, handle_setautogameinterval,
    handle_setautoeventinterval, handle_setautoeventduration,
    handle_gameconfig, handle_autogamesowner, handle_stopautogames,
    handle_fixautogames,
    try_direct_answer, handle_gamehint, handle_revealanswer,
)
from modules.gold import (
    handle_goldtip, handle_goldrefund,
    handle_goldrain as _handle_goldrain_legacy, handle_goldrainall,
    handle_goldraineligible,
    handle_goldrainrole, handle_goldrainvip,
    handle_goldraintitle, handle_goldrainbadge,
    handle_goldrainlist,
    handle_goldwallet, handle_goldtips, handle_goldtx,
    handle_pendinggold, handle_confirmgoldtip,
    handle_setgoldrainstaff, handle_setgoldrainmax,
    handle_goldhelp,
    handle_goldtipbots, handle_goldtipall,
    set_bot_identity, get_bot_user_id, get_bot_username,
    add_to_room_cache, remove_from_room_cache,
    refresh_room_cache,
)
from modules.gold_rain import (
    handle_goldrain,
    handle_goldrainstatus,
    handle_cancelgoldrain,
    handle_goldrainhistory,
    handle_goldraininterval,
    handle_setgoldraininterval,
    handle_goldrainreplace,
    handle_goldrainpace,
    handle_setgoldrainpace,
)
from modules.msg_cap import handle_msgcap, handle_setmsgcap
from modules.time_exp import (
    record_join      as time_exp_record_join,
    record_leave     as time_exp_record_leave,
    record_activity  as time_exp_record_activity,
    time_exp_loop,
    handle_settimeexp,
    handle_settimeexpcap,
    handle_settimeexptick,
    handle_settimeexpbonus,
    handle_timeexpstatus,
    handle_setallowbotxp,
)
from modules.display_settings import (
    handle_displaybadges,
    handle_displaytitles,
    handle_displayformat,
    handle_displaytest,
)
from modules.bot_welcome import (
    send_bot_welcome,
    handle_botwelcome,
    handle_setbotwelcome,
    handle_resetbotwelcome,
    handle_previewbotwelcome,
    handle_botwelcomes,
    handle_bios,
    handle_checkbios,
    handle_checkonboarding,
)
from modules.gold_tips import (
    handle_goldtipsettings,
    handle_setgoldrate,
    handle_goldtiplogs,
    handle_mygoldtips,
    handle_goldtipstatus,
    handle_incoming_gold_tip,
    handle_tipcoinrate,
    handle_settipcoinrate,
    handle_bottiplogs,
    handle_mingoldtip,
    handle_setmingoldtip,
    handle_tiplb,
    handle_roomtiplb,
    handle_tipreceiverlb,
    handle_tipadmin,
)
from modules.luxe_admin import (
    handle_addtickets, handle_removetickets, handle_settickets,
    handle_sendtickets, handle_ticketbalance, handle_ticketlogs,
    handle_ticketadmin, handle_ticketrate,
)
from modules.tip_audit import (
    handle_tipaudit,
    handle_tipauditdetails,
    handle_conversionlogs,
)
from modules.mining import (
    handle_mine, handle_tool, handle_upgradetool,
    handle_mineprofile, handle_mineinv, handle_sellores, handle_sellore,
    handle_minelb, handle_mineshop, handle_minebuy,
    handle_useluckboost, handle_usexpboost, handle_useenergy,
    handle_craft, handle_minedaily,
    handle_miningevent, handle_miningevents,
    handle_startminingevent, handle_stopminingevent,
    handle_miningadmin, handle_mining_toggle,
    handle_setminecooldown, handle_setmineenergycost, handle_setminingenergy,
    handle_addore, handle_removeore,
    handle_settoollevel, handle_setminelevel,
    handle_addminexp, handle_setminexp, handle_resetmining,
    handle_miningroomrequired,
    handle_orelist, handle_oreprices, handle_oreinfo, handle_minehelp,
    handle_simannounce,
    handle_forcedrop, handle_forcedropore, handle_forcedropstatus,
    handle_clearforcedrop,
    handle_orebook, handle_oremastery, handle_claimoremastery, handle_orestats,
    handle_contracts, handle_job, handle_deliver, handle_claimjob, handle_rerolljob,
    handle_mineconfig, handle_mineeventstatus,
    handle_minepanel,
    # A2: ore chance commands
    handle_minechances,
    handle_orechances, handle_orechance,
    handle_setorechance, handle_setraritychance, handle_reloadorechances,
    handle_automine, handle_autominestatus, handle_autominesettings,
    handle_setautomine, handle_setautomineduration,
    handle_setautomineattempts, handle_setautominedailycap,
    stop_automine_for_user, startup_automine_recovery,
    MINE_HELP_PAGES,
    handle_mineluck, handle_mineadmin,
)
from modules.economy import (
    handle_economypanel,
    handle_economysettings,
    handle_economycap,
    handle_setraritycap,
    handle_resetraritycaps,
    handle_payoutlogs,
    handle_biggestpayouts,
)
from modules.fishing import (
    handle_fish, handle_fishlist, handle_fishprices, handle_fishinfo,
    handle_myfish, handle_sellfish, handle_sellallfish,
    handle_sellfishrarity, handle_fishbag, handle_fishbook,
    handle_fishautosell, handle_fishautosellrare,
    handle_fishlevel, handle_fishstats, handle_fishboosts, handle_fishingevents,
    handle_fishchances, handle_fishhelp, handle_topfish, handle_topweightfish,
    handle_rods, handle_myrod, handle_rodshop, handle_buyrod,
    handle_equiprod, handle_rodinfo, handle_rodstats, handle_rodupgrade,
    handle_autofish, handle_autofishstatus, handle_autofishsettings,
    handle_setautofish, handle_setautofishduration,
    handle_setautofishattempts, handle_setautofishdailycap,
    stop_autofish_for_user, startup_autofish_recovery,
    handle_forcedropfish, handle_forcedropfishitem,
    handle_fishluck, handle_fishadmin,
    handle_forcedropfishstatus, handle_forcedropfishdebug,
    handle_clearforcedropfish,
    handle_fishpanel,
    handle_setfishcooldown, handle_setfishweights,
    handle_setfishweightscale, handle_setfishannounce,
    handle_setfishrarityweightrange,
)
from modules.collection import (
    handle_collection, handle_topcollectors, handle_rarelog,
    handle_lastminesummary, handle_lastfishsummary, handle_collectionhelp,
    handle_enabledm,
)
from modules.safe_mode import handle_safemode, handle_active, handle_repair
from modules.leaderboards import (
    handle_toprich, handle_topminers, handle_topfishers, handle_topstreaks,
    handle_toptippers, handle_toptipped, handle_p2pgolddebug,
)
from modules.player_cmds import (
    handle_menu, handle_cooldowns_cmd, handle_rewards_inbox,
    handle_wherebots, handle_updates, handle_rankup,
)
from modules.suggestions import (
    handle_suggest, handle_suggestions,
    handle_bugreport, handle_bugreports,
    handle_eventvote, handle_voteevent,
)
from modules.sub_notif import (
    handle_notif, handle_notifon, handle_notifoff, handle_notifall,
    handle_notifdm, handle_opennotifs,
    handle_subnotify, handle_subnotifyinvite, handle_subnotifystatus,
    handle_testnotify as handle_sub_testnotify,
    handle_setsubnotifycooldown,
    handle_notif_dispatch_channel,
    handle_notifpreview,
)
from modules.first_find import (
    handle_firstfindrewards, handle_setfirstfind, handle_setfirstfinditem,
    handle_startfirstfind, handle_stopfirstfind,
    handle_firstfindstatus, handle_firstfindwinners, handle_resetfirstfind,
    handle_firstfindpending, handle_paypendingfirstfind,
    startup_firstfind_announcer, startup_firstfind_banker,
)
from modules.boost_admin import handle_boostadmin
from modules.big_announce import (
    handle_setbigannounce, handle_bigannouncestatus,
    handle_setbotbigreact, handle_bigannounce_help,
    handle_previewannounce,
    startup_big_announce_reactor,
)
from modules.control_panel import (
    handle_control, handle_ownerpanel, handle_managerpanel,
    handle_status, handle_roomstatus, handle_economystatus,
    handle_quicktoggles, handle_toggle,
)
from modules.multi_bot import (
    should_this_bot_handle, should_this_bot_run_module,
    BOT_MODE,
    start_heartbeat_loop as start_multibot_heartbeat,
    get_offline_message, send_startup_announce,
    check_startup_safety,
    handle_bots_live, handle_botstatus_cluster,
    handle_botmodules, handle_commandowners,
    handle_enablebot, handle_disablebot,
    handle_setbotmodule, handle_setcommandowner, handle_botfallback,
    handle_botstartupannounce, handle_multibothelp,
    handle_startupannounce, handle_modulestartup, handle_startupstatus,
    handle_setmainmode,
    handle_taskowners, handle_activetasks,
    handle_taskconflicts, handle_fixtaskowners,
    handle_restoreannounce, handle_restorestatus,
)
from modules.bot_health import (
    handle_bothealth, handle_modulehealth, handle_deploymentcheck,
    handle_botlocks, handle_clearstalebotlocks,
    handle_botheartbeat, handle_moduleowners,
    handle_botconflicts, handle_fixbotowners,
    handle_dblockcheck,
)
# 3.3A — Old EmceeBot AI assistant quarantined. Rebuilt as AceSinatra.
# Old file: modules/ai_assistant_old_broken.py (kept for reference, do not import)
from modules.ai_assistant_core import handle_acesinatra
from modules.room_assistant import (
    handle_room_assistant_chat,
    handle_room_assistant_whisper,
    handle_unknown_command,
)
from modules.bot_modes import (
    handle_botmode, handle_botmodes, handle_botprofile,
    handle_botprefix, handle_categoryprefix,
    handle_setbotprefix, handle_setbotdesc, handle_setbotoutfit,
    handle_botoutfit, handle_botoutfits,
    handle_dressbot, handle_savebotoutfit,
    handle_copyoutfit, handle_wearuseroutfit,
    handle_renamebotoutfit, handle_clearbotoutfit,
    handle_copymyoutfit, handle_copyoutfitfrom,
    handle_savemyoutfit, handle_wearoutfit,
    handle_myoutfits, handle_myoutfitstatus, handle_outfitredirect,
    handle_direct_bot_outfit_chat, handle_directoutfittest,
    handle_botoutfitdebug,
    handle_createbotmode, handle_deletebotmode, handle_assignbotmode,
    handle_bots, handle_botinfo, handle_botoutfitlogs,
    handle_botmodehelp,
    handle_botmessageformat, handle_setbotmessageformat,
)
from modules.msg_test import (
    handle_msgtest, handle_msgboxtest, handle_msgsplitpreview,
)
from modules.tele import (
    handle_tele, handle_create_tele, handle_delete_tele,
    handle_summon,
    handle_setrolespawn, handle_rolespawn, handle_rolespawns, handle_delrolespawn,
    handle_tag,
    handle_teleporthelp_tele,
    handle_autospawn, handle_roles, handle_rolemembers, get_autospawn_spawn_for_user,
)
from modules.party_tip import (
    handle_party,
    handle_pton,
    handle_ptoff,
    handle_ptstatus,
    handle_ptenable,
    handle_ptdisable,
    handle_ptwallet,
    handle_ptadd,
    handle_ptremove,
    handle_ptclear,
    handle_ptlist,
    handle_ptlimits,
    handle_ptlimit,
    handle_tip as handle_party_tip,
    handle_tipall_redirect,
)
from modules.economy_audit import (
    handle_economyaudit, handle_gameprices, handle_gameprice,
    handle_setgameprice, handle_messageaudit, handle_helpaudit,
)
from modules.jail_system import (
    handle_jail, handle_bail, handle_jailstatus, handle_jailtime,
    handle_jailhelp, handle_unjail, handle_jailactive, handle_jailadmin,
    handle_jailsetcost, handle_jailsetmax, handle_jailsetmin,
    handle_jailsetbailmultiplier, handle_jailprotectstaff, handle_jaildebug,
    handle_setjailspot, handle_setjailguardspot, handle_setsecurityidle,
    handle_setjailreleasespot,
    handle_jail_confirm, handle_jail_cancel,
    startup_jail_recovery,
)
from modules.jail_enforcement import enforce_jail_on_rejoin, is_jailed
from modules.room_utils import (
    handle_tpme, handle_tp, handle_tphere, handle_goto,
    handle_bring, handle_bringall, handle_tpall,
    handle_tprole, handle_tpvip, handle_tpstaff,
    handle_selftp, handle_groupteleport,
    handle_spawns, handle_spawn, handle_setspawn, handle_delspawn,
    handle_spawninfo, handle_setspawncoords, handle_savepos,
    handle_emotes, handle_emote, handle_stopemote, handle_emoteinfo,
    handle_setbotspawn, handle_setbotspawnhere, handle_botspawns,
    handle_clearbotspawn, apply_bot_spawn,
    teleport_bot_to_saved_spawn, handle_returnbots,
    handle_mypos, handle_positiondebug,
    handle_dance, handle_wave, handle_sit, handle_clap,
    handle_forceemote, handle_forceemoteall,
    handle_loopemote, handle_stoploop, handle_stopallloops,
    handle_synchost, handle_syncdance, handle_stopsync,
    handle_publicemotes, handle_forceemotes, handle_setemoteloopinterval,
    handle_heart, handle_hearts, handle_heartlb,
    handle_giveheart, handle_reactheart,
    handle_hug, handle_kiss, handle_slap, handle_punch,
    handle_highfive, handle_boop, handle_waveat, handle_cheer,
    handle_social, handle_blocksocial, handle_unblocksocial,
    handle_followme, handle_follow, handle_stopfollow, handle_followstatus,
    handle_alert, handle_staffalert, handle_vipalert, handle_clearalerts,
    handle_welcome, handle_setwelcome, handle_welcometest,
    handle_resetwelcome, handle_welcomeinterval,
    handle_intervals, handle_addinterval, handle_delinterval,
    handle_interval, handle_intervaltest,
    handle_repeatmsg, handle_stoprepeat, handle_repeatstatus,
    handle_players, handle_roomlist, handle_online,
    handle_staffonline, handle_vipsinroom, handle_rolelist,
    handle_kick, handle_ban, handle_tempban, handle_unban,
    handle_bans, handle_modlog,
    handle_roomsettings, handle_setroomsetting,
    handle_roomlogs,
    handle_boostroom, handle_startmic, handle_micstatus,
    handle_roomhelp, handle_teleporthelp, handle_emotehelp,
    handle_alerthelp, handle_welcomehelp, handle_socialhelp,
    send_welcome_if_needed, update_user_position,
    start_interval_loop,
)


# ---------------------------------------------------------------------------
# Command sets
# ---------------------------------------------------------------------------

ECONOMY_COMMANDS     = {
    "balance", "bal", "b", "coins", "coin", "money", "daily",
    "leaderboard", "lb", "top",
    "toprich", "topminers", "topfishers", "topstreaks",
    "topdonors", "topdonators", "donators",
    "toptippers", "toptipped", "toptipreceivers",
}
PROFILE_COMMANDS     = {
    "profile", "me", "whois", "pinfo",
    "stats", "badges", "titles",
    "privacy",
    "level", "xpleaderboard",
}
GAME_COMMANDS        = {"trivia", "scramble", "riddle", "coinflip"}
SHOP_COMMANDS        = {
    "shop", "buy", "equip", "myitems", "inventory", "inv", "badgeinfo", "titleinfo",
    "mybadges", "unequip",
    "badgemarket", "badgelist", "badgebuy", "badgecancel",
    "mybadgelistings", "badgeprices",
}
ACHIEVEMENT_COMMANDS = {"achievements", "claimachievements"}
BJ_COMMANDS          = {
    "bj", "rbj",
    "bjoin", "bt", "bh", "bs", "bd", "bsp", "bi", "blimits", "bstats", "bhand",
    "bjh", "bjs", "bjd", "bjsp", "bjhand",
    "rjoin", "rt", "rh", "rs", "rd", "rsp", "rshoe", "rlimits", "rstats", "rhand",
    "rbjh", "rbjs", "rbjd", "rbjsp", "rbjhand",
    # Easy aliases / universal shortcuts
    "blackjack", "bjbet", "bet", "hit", "stand", "stay", "double", "split",
    "insurance", "surrender", "shoe", "bjshoe",
    # Card display / rules / bonus info
    "bjcards", "blackjackcards", "cardmode", "bjcardmode",
    "bjrules",
    "bjbonus", "bjbonussetting", "bjbonussettings",
}
BANK_PLAYER_COMMANDS = {"bank", "send", "transactions", "bankstats", "banknotify"}

BANK_ADMIN_SET_CMDS = {
    "setminsend", "setmaxsend",
    "setsendlimit", "setnewaccountdays", "setminlevelsend",
    "setmintotalearned", "setmindailyclaims",
    "setsendtax", "sethighriskblocks",
}

# Staff command tiers
MOD_ONLY_CMDS = {
    "resetgame", "announce", "viewtx", "bankwatch",
    "audit", "auditbank", "auditcasino", "auditeconomy", "audithelp",
    "auditlog",
    "economysettings",
    "reports", "reportinfo", "closereport", "reportwatch",
    "warn", "warnings",
    "staffdash", "stafftools",
    "staffnote", "staffnotes",
    "permissioncheck", "rolecheck",
    "replog",
    "allstaff",
    "subscribers",
    "mute", "unmute", "mutes",
    "profileprivacy",
}

MANAGER_ONLY_CMDS = {
    "automod",
    # ── Bot health / multi-bot visibility (manager+) ──────────────────────────
    "bothealth", "modulehealth", "deploymentcheck",
    "botheartbeat", "botconflicts", "botlocks", "moduleowners",
    "setpokerbuyin", "setpokerplayers", "setpokerlobbytimer",
    "setpokertimer", "setpokerturntimer", "setpokerraise",
    "setpokerdailywinlimit", "setpokerdailylosslimit",
    "resetpokerlimits", "setpokerlimits",
    "setpokerblinds", "setpokerante", "setpokernexthandtimer",
    "setpokermaxstack", "setpokeridlestrikes",
    "pokerdebug", "pokerfix", "pokerrefundall", "pokercleanup",
    "confirmclosepoker",
    "pokerdashboard", "pdash", "pokeradmin",
    "pokerpause", "pokerresume",
    "pokerforceadvance", "pokerforceresend",
    "pokerturn", "pokerpots", "pokeractions", "pokerstylepreview",
    "pokerresetturn", "pokerresethand", "pokerresettable",
    "casinointegrity", "integritylogs", "carddeliverycheck",
    "setpokercardmarker",
    "banksettings",
    "setbjminbet", "setbjmaxbet", "setbjcountdown", "setbjturntimer",
    "setbjactiontimer", "setbjmaxsplits",
    "setbjdailywinlimit", "setbjdailylosslimit",
    "setbjbonus", "setbjbonuscap",
    "setbjbonuspair", "setbjbonuscolor", "setbjbonusperfect",
    "setbjinsurance",
    "setbigannounce", "setbigreact", "setbotbigreact",
    "setrbjdecks", "setrbjminbet", "setrbjmaxbet", "setrbjcountdown",
    "setrbjshuffle", "setrbjblackjackpayout", "setrbjwinpayout", "setrbjturntimer",
    "setrbjactiontimer", "setrbjmaxsplits",
    "setrbjdailywinlimit", "setrbjdailylosslimit",
    "setgametimer", "setautogameinterval",
    "setautoeventinterval", "setautoeventduration",
    "gameconfig",
    "casinosettings", "casinolimits", "casinotoggles",
    "setbjlimits", "setrbjlimits",
    "resetbjlimits", "resetrbjlimits",
    # ── Gold Rain new commands (BankerBot) ────────────────────────────────────
    "goldrainstatus", "cancelgoldrain", "goldrainhistory",
    "goldraininterval", "setgoldraininterval", "goldrainreplace",
    "goldrainpace", "setgoldrainpace",
    "raingold", "goldstorm", "golddrop",
    # ── Message cap testing (EmceeBot / host) ─────────────────────────────────
    "msgcap", "setmsgcap",
}

# ── First-find + announce public read commands ────────────────────────────
FIRSTFIND_COMMANDS = {
    "firstfindreward", "firstfindrewards", "firstfindstatus", "firstfindcheck",
    "setfirstfind", "setfirstfinditem", "setfirstfindreward", "resetfirstfind",
    "startfirstfind", "stopfirstfind", "firstfindwinners",
    "firstfindpending", "firstfindpay", "paypendingfirstfind", "retryfirstfind",
    "bigannounce", "bigannouncestatus", "setbigannounce",
    "setbigreact", "setbotbigreact", "previewannounce",
}

TIP_COMMANDS = {"tiprate", "tipstats", "tipleaderboard"}
TIP_ADMIN_CMDS = {"settiprate", "settipcap", "settiptier", "settipautosub", "settipresubscribe"}

ADMIN_ONLY_CMDS = {
    "setrules",
    # ── Bot health repair (admin+) ────────────────────────────────────────────
    "dblockcheck", "clearstalebotlocks", "fixbotowners",
    # ── Coins ────────────────────────────────────────────────────────────────
    "addcoins", "removecoins",
    "setcoins", "editcoins", "resetcoins",
    # ── Event coins ──────────────────────────────────────────────────────────
    "addeventcoins", "removeeventcoins",
    "seteventcoins", "editeventcoins", "reseteventcoins",
    # ── XP / Level ───────────────────────────────────────────────────────────
    "addxp", "removexp",
    "setxp", "editxp", "resetxp",
    "setlevel", "editlevel", "addlevel", "removelevel",
    "promotelevel", "demotelevel",
    # ── Reputation ───────────────────────────────────────────────────────────
    "addrep", "removerep",
    "setrep", "editrep", "resetrep",
    # ── Titles ───────────────────────────────────────────────────────────────
    "givetitle", "removetitle", "settitle", "cleartitle",
    # ── Badges ───────────────────────────────────────────────────────────────
    "givebadge", "removebadge", "removebadgefrom", "setbadge", "clearbadge",
    # ── Emoji Badge Market (admin) ────────────────────────────────────────────
    "addbadge", "editbadgeprice",
    "setbadgepurchasable", "setbadgetradeable", "setbadgesellable",
    "giveemojibadge", "badgecatalog", "badgeadmin",
    "setbadgemarketfee", "badgemarketlogs",
    # ── VIP ──────────────────────────────────────────────────────────────────
    "addvip", "removevip", "vips", "setvipprice",
    # ── Casino resets ─────────────────────────────────────────────────────────
    "resetbjstats", "resetrbjstats", "resetpokerstats", "resetcasinostats",
    # ── Roles ────────────────────────────────────────────────────────────────
    "addmanager", "removemanager",
    "addmoderator", "removemoderator",
    # ── Bank admin ───────────────────────────────────────────────────────────
    "bankblock", "bankunblock",
    "ledger",
    # ── Profile ──────────────────────────────────────────────────────────────
    "profileadmin", "resetprofileprivacy",
    # ── Commands & audit ─────────────────────────────────────────────────────
    "allcommands",
    "checkcommands", "checkhelp",
    "missingcommands", "routecheck", "silentcheck", "commandtest",
    "fixcommands", "testcommands",
    "commandintegrity", "commandrepair", "commandissues", "launchcheck",
    "currencycheck",
    "commandtestall", "ctall", "commandtestgroup", "ctgroup",
    # ── Economy settings ─────────────────────────────────────────────────────
    "setdailycoins", "setgamereward", "settransferfee",
    # ── Event aliases ────────────────────────────────────────────────────────
    "eventstart", "eventstop",
    # ── Moderation ───────────────────────────────────────────────────────────
    "clearwarnings",
    # ── Notifications ────────────────────────────────────────────────────────
    "dmnotify", "announce_subs", "announce_vip", "announce_staff",
    "healthcheck",
    "notifyuser", "broadcasttest",
    "debugnotify", "testnotify", "pendingnotify", "clearpendingnotify",
    # ── Logs ─────────────────────────────────────────────────────────────────
    "adminlogs", "adminloginfo",
} | BANK_ADMIN_SET_CMDS | TIP_ADMIN_CMDS

MANAGER_ONLY_CMDS = MANAGER_ONLY_CMDS | {"notifystats", "notifyprefs", "dailyadmin"}

OWNER_ONLY_CMDS = {
    "addadmin", "removeadmin", "admins", "setmaxbalance",
    "addowner", "removeowner",
    "goldtip", "tipgold", "goldreward", "rewardgold",
    "goldrefund", "goldrain", "goldrainall", "goldraineligible",
    "goldrainrole", "goldrainvip", "goldraintitle", "goldrainbadge", "goldrainlist",
    "goldwallet", "goldtips", "goldtx", "pendinggold",
    "confirmgoldtip", "setgoldrainstaff", "setgoldrainmax",
    "goldtipbots", "goldtipall",
    "debugsub",
    "debugtips",
    "restarthelp", "restartstatus",
    "softrestart",
    "restartbot",
    "testnotifyall",
    "fixcommandregistry",
    "previewannounce",
    "launchcheck",
}

STAFF_CMDS = MOD_ONLY_CMDS | MANAGER_ONLY_CMDS | ADMIN_ONLY_CMDS | OWNER_ONLY_CMDS

ALL_KNOWN_COMMANDS = (
    {
        "help", "answer",
        "owners",
        "casinohelp", "gamehelp", "coinhelp", "bankerhelp", "profilehelp",
        "shophelp", "progresshelp", "bankhelp", "eventhelp",
        "economydbcheck", "economyrepair",
        "bjhelp", "rbjhelp", "rephelp", "autohelp",
        "casinoadminhelp", "bankadminhelp",
        "audithelp", "reporthelp", "maintenancehelp",
        "viphelp", "tiphelp", "roleshelp",
        "staffhelp", "modhelp", "managerhelp", "adminhelp", "ownerhelp",
        "casino", "managers", "moderators",
        "howtoplay", "gameguide", "games",
        "quests", "claimquest",
        "dailyquests", "weeklyquests", "questhelp",
        "event", "events", "eventhelp", "eventstatus",
        "nextevent", "next", "schedule", "eventloop",
        "eventadmin", "startevent", "stopevent",
        "eventpoints", "eventshop", "buyevent",
        "autogames", "autoevents", "gameconfig",
        "autogamesowner", "stopautogames", "killautogames", "fixautogames",
        "report", "bug", "myreports",
        "rep", "reputation", "repstats", "toprep", "repleaderboard",
        # Poker — full commands
        "poker", "pokerhelp", "pokerstats", "pokerlb", "pokerdebug",
        "pokerfix", "pokerrefundall", "pokercleanup",
        "setpokerbuyin", "setpokerplayers", "setpokerlobbytimer",
        "setpokertimer", "setpokerturntimer", "setpokerraise",
        "setpokerdailywinlimit", "setpokerdailylosslimit",
        "resetpokerlimits", "setpokerlimits",
        "setpokerblinds", "setpokerante", "setpokernexthandtimer",
        "setpokermaxstack", "setpokeridlestrikes",
        # Poker — short aliases + persistent-table commands
        "p", "pj", "pt", "ptable", "ph", "pcards", "po", "podds",
        "resendcards", "cards", "pokerdealstatus", "pokerplayers",
        "check", "ch", "call", "ca", "raise", "r", "fold", "f",
        "allin", "all-in", "shove",
        "pp", "pplayers", "pstats", "plb", "pleaderboard",
        "phelp", "pokerlb", "pokerleaderboard", "pleaderboard",
        "sitout", "sitin", "sitback", "rebuy", "pstacks", "mystack",
        "lasthand", "handlog", "pokerguide",
        "botstatus", "dbstats", "backup",
        "maintenance", "reloadsettings", "cleanup",
        "restarthelp", "restartstatus", "softrestart", "restartbot",
        "casinosettings", "casinolimits", "casinotoggles",
        "setbjlimits", "setrbjlimits",
        "wallet", "w", "dash", "dashboard", "casinodash", "mycasino",
        "goldhelp", "goldtipbots", "tipall", "goldtipall", "confirmcasinoreset",
        "tip", "tiprate", "tipstats", "tipleaderboard", "debugtips",
        "vipshop", "buyvip", "vipstatus",
        "me", "whois", "pinfo", "stats", "badges", "titles", "privacy",
        "profileadmin", "profileprivacy", "resetprofileprivacy",
        "allstaff", "allcommands", "checkcommands", "commandaudit",
        "missingcommands", "routecheck", "silentcheck", "commandtest",
        "fixcommands", "testcommands", "commandintegrity", "commandrepair",
        "commandissues", "fixcommandregistry",
        "notifications", "clearnotifications",
        "delivernotifications", "pendingnotifications",
        "subscribe", "sub", "unsubscribe", "unsub", "substatus", "subhelp",
        "notif", "notifon", "notifoff", "notifstatus", "notifpreview",
        "notifysettings", "notify", "notifyhelp",
        "notifystats", "notifyprefs", "notifyuser", "broadcasttest",
        "debugnotify", "testnotify", "testnotifyall",
        "pendingnotify", "clearpendingnotify",
        "dailyadmin",
        "rules", "setrules", "automod",
        "announce_subs", "announce_vip", "announce_staff", "dmnotify",
        "subscribers",
        # Admin power commands (aliases included — STAFF_CMDS covers the rest)
        "adminpanel", "checkhelp",
        "editeventcoins",
        "editxp",
        "editlevel", "removelevel", "promotelevel", "demotelevel",
        "editrep",
        "settitle", "cleartitle",
        "setbadge", "clearbadge", "removebadgefrom",
        "setvipprice",
        "adminloginfo",
        # Emoji Badge Market
        "addbadge", "editbadgeprice",
        "setbadgepurchasable", "setbadgetradeable", "setbadgesellable",
        "giveemojibadge", "badgecatalog", "badgeadmin",
        "setbadgemarketfee", "badgemarketlogs",
        "mybadges", "unequip",
        "badgemarket", "badgelist", "badgebuy", "badgecancel",
        "mybadgelistings", "badgeprices",
        # Numbered shop system
        "buyitem", "purchase",
        "confirmbuy", "cancelbuy",
        "shopadmin", "shoptest",
        "setshopconfirm", "seteventconfirm",
        "marketbuy",
        # Public help tools
        "mycommands", "helpsearch", "start", "guide",
        "new", "activities", "roominfo", "newbie", "whatdoido",
        # Paged coin help
        "pokerhelp",
        # Mining game — player commands
        "mine", "m", "dig",
        "tool", "pickaxe",
        "upgradetool", "upick",
        "mineprofile", "mp", "minerank",
        "mineinv", "ores",
        "sellores", "sellore",
        "minelb",
        "mineshop", "minebuy",
        "useluckboost", "usexpboost", "useenergy",
        "craft",
        "minedaily",
        "miningevent", "miningevents",
        "orelist",
        "orebook", "oremastery", "claimoremastery", "orestats",
        "collection", "mybook", "collectbook",
        "topcollectors", "topore", "toporecollectors",
        "rarelog", "lastminesummary", "collectionhelp", "bookhelp",
        "mineluck", "minestack", "mineadmin",
        "enabledm", "summarydm",
        "contracts", "miningjobs", "job", "deliver", "claimjob", "rerolljob",
        "minehelp",
        # Mining game — staff commands
        "mining",
        "startminingevent", "stopminingevent",
        "setminecooldown", "setmineenergycost", "setminingenergy",
        "addore", "removeore",
        "settoollevel", "setminelevel",
        "addminexp", "setminexp",
        "resetmining", "miningadmin",
        "miningroomrequired",
        # ── Room utility — public ─────────────────────────────────────────────
        "players", "roomlist", "online", "staffonline", "vipsinroom", "rolelist",
        "emotes", "emote", "stopemote", "dance", "wave", "sit", "clap",
        "heart", "hearts", "heartlb", "giveheart", "reactheart",
        "hug", "kiss", "slap", "punch", "highfive", "boop", "waveat", "cheer",
        "social", "blocksocial", "unblocksocial", "socialhelp",
        "spawns", "spawn",
        "roomhelp", "teleporthelp", "emotehelp", "alerthelp", "welcomehelp",
        # ── Room utility — staff ──────────────────────────────────────────────
        "tpme", "tp", "tphere", "goto", "bring", "bringall", "tpall",
        "tprole", "tpvip", "tpstaff", "selftp", "groupteleport",
        "setspawn", "savepos", "delspawn", "spawninfo", "setspawncoords",
        "forceemote", "forceemoteall", "loopemote", "stoploop", "stopallloops",
        "synchost", "syncdance", "stopsync",
        "publicemotes", "forceemotes", "setemoteloopinterval",
        "followme", "follow", "stopfollow", "followstatus",
        "alert", "staffalert", "vipalert", "roomalert", "clearalerts",
        "welcome", "setwelcome", "welcometest", "resetwelcome", "welcomeinterval",
        "intervals", "addinterval", "delinterval", "interval", "intervaltest",
        "repeatmsg", "stoprepeat", "repeatstatus",
        "roomsettings", "setroomsetting", "roomlogs",
        "kick", "ban", "tempban", "unban", "bans", "modlog",
        "boostroom", "roomboost", "startmic", "micstart", "micstatus",
        # ── Bot modes — public ────────────────────────────────────────────────
        "botmode", "botmodes", "botprofile", "bots", "botinfo",
        "botoutfit", "botoutfits", "botmodehelp",
        # ── Bot modes — staff ─────────────────────────────────────────────────
        "botprefix", "categoryprefix",
        "setbotprefix", "setbotdesc", "setbotoutfit",
        "dressbot", "savebotoutfit",
        "copyoutfit", "wearuseroutfit",
        "renamebotoutfit", "clearbotoutfit",
        "copymyoutfit", "copyoutfitfrom",
        "savemyoutfit", "wearoutfit",
        "myoutfits", "myoutfitstatus",
        "directoutfittest",
        "createbotmode", "deletebotmode", "assignbotmode",
        "botoutfitlogs",
        # ── Control panel ─────────────────────────────────────────────────────
        "control", "adminpanel", "ownerpanel", "managerpanel",
        "status", "roomstatus", "economystatus",
        "quicktoggles", "toggle",
        # ── Multi-bot system ──────────────────────────────────────────────────
        "botmodules", "commandowners", "multibothelp",
        "enablebot", "disablebot", "setbotmodule",
        "setcommandowner", "botfallback", "botstartupannounce",
        "startupannounce", "modulestartup", "startupstatus",
        # ── Multi-bot mode control ────────────────────────────────────────────
        "setmainmode",
        # ── Bot health / deployment checks ────────────────────────────────────
        "bothealth", "modulehealth", "deploymentcheck",
        "crashlogs", "missingbots",
        "botlocks", "clearstalebotlocks",
        "botheartbeat", "moduleowners",
        "botconflicts", "fixbotowners", "dblockcheck",
        # ── Task ownership / restore announce ─────────────────────────────────
        "taskowners", "activetasks", "taskconflicts", "fixtaskowners",
        "restoreannounce", "restorestatus",
        "commandissues",
        # ── AI assistant (3.3A — ChillTopiaMC AI, say "ai ...") ──────────────
        "ai",
        # ── Emote extensions ──────────────────────────────────────────────────
        "emoteinfo",
        # ── Bot spawns ────────────────────────────────────────────────────────
        "setbotspawn", "setbotspawnhere", "botspawns", "clearbotspawn",
        "returnbots", "botshome",
        "mypos", "positiondebug",
        # ── Events (new) ──────────────────────────────────────────────────────
        "adminsblessing", "adminblessing", "eventresume",
        "autogamestatus", "autogameresume",
        # ── Mining (new) ──────────────────────────────────────────────────────
        "mineconfig", "mineeventstatus",
        # ── Mining weight LB (new) ────────────────────────────────────────────
        "oreweightlb", "weightlb", "heaviest",
        "myheaviest", "oreweights", "topweights",
        "setweightlbmode",
        # ── Mining announce settings (new) ────────────────────────────────────
        "mineannounce", "setmineannounce",
        "setoreannounce", "oreannounce",
        "mineannouncesettings",
        # ── Mining weight admin settings (new) ────────────────────────────────
        "mineweights", "setmineweights",
        "setweightscale", "setrarityweightrange",
        "oreweightsettings",
        # ── Bulk command testing (new) ─────────────────────────────────────────
        "commandtestall", "ctall",
        "commandtestgroup", "ctgroup",
        # ── Poker pace / stack (new) ──────────────────────────────────────────
        "pokermode", "pokerpace", "setpokerpace",
        "pokerstacks", "setpokerstack", "dealstatus",
        # ── Poker dashboard + controls (new) ──────────────────────────────────
        "pokerdashboard", "pdash", "pokeradmin",
        "pokerpause", "pokerresume",
        "pokerforceadvance", "pokerforceresend",
        "pokerturn", "pokerpots", "pokeractions",
        "pokerresetturn", "pokerresethand", "pokerresettable",
        # ── Mining panel (new) ────────────────────────────────────────────────
        "minepanel", "miningpanel",
        # ── Time EXP bot exclusion (new) ──────────────────────────────────────
        "setallowbotxp",
        # ── Per-bot welcome messages (new) ────────────────────────────────────
        "botwelcome", "setbotwelcome", "resetbotwelcome",
        "previewbotwelcome", "botwelcomes",
        # ── Bio + onboarding audit commands (3.0C) ────────────────────────────
        "bios", "checkbios", "checkonboarding",
        # ── Gold tip commands (new) ────────────────────────────────────────────
        "goldtipsettings", "setgoldrate",
        "goldtiplogs", "mygoldtips", "goldtipstatus",
        "tipcoinrate", "settipcoinrate",
        "bottiplogs", "mingoldtip", "setmingoldtip",
        "tipgold", "goldreward", "rewardgold",
        "tiplb", "tipleaderboard", "bottiplb", "bottipleaderboard",
        "roomtiplb", "roomtipleaderboard", "alltiplb", "alltipleaderboard",
        "tipreceiverlb", "topreceivers",
        # ── Fishing force drop (owner-only, handled by fisher bot) ─────────────
        "forcedropfish", "forcedropfishitem", "forcedropfishstatus",
        "forcedropfishdebug", "clearforcedropfish", "clearforceddropfish",
        "forcefishdrop", "forcefish", "forcefishdropfish",
        "forcefishstatus", "clearforcefish",
        "topfishcollectors", "fishcollectors", "lastfishsummary",
        "fishluck", "fishstack", "fishadmin",
        "fishpanel", "fishingpanel",
        "setfishcooldown", "setfishweights", "setfishweightscale",
        "setfishannounce", "setfishrarityweightrange",
        "boostadmin", "luck", "myluck",
    }
    | ECONOMY_COMMANDS | PROFILE_COMMANDS | GAME_COMMANDS
    | SHOP_COMMANDS | ACHIEVEMENT_COMMANDS | BJ_COMMANDS
    | BANK_PLAYER_COMMANDS | STAFF_CMDS
)

TIME_EXP_COMMANDS: frozenset[str] = frozenset({
    "settimeexp", "settimeexpcap", "settimeexptick",
    "settimeexpbonus", "timeexpstatus", "setallowbotxp",
})
DISPLAY_COMMANDS: frozenset[str] = frozenset({
    "displaybadges", "displaytitles", "displayformat", "displaytest",
})
NEW_PROJECT_COMMANDS: frozenset[str] = frozenset({
    # Gold Rain (new system — BankerBot)
    "raingold", "goldstorm", "golddrop",
    "goldrainstatus", "cancelgoldrain",
    "goldrainhistory", "goldraininterval",
    "setgoldraininterval", "goldrainreplace",
    "goldrainpace", "setgoldrainpace",
    "msgcap", "setmsgcap",
    # System dashboard
    "botdashboard", "botsystem",
    # Reward center
    "rewardpending", "pendingrewards", "rewardlogs", "markrewardpaid",
    "economyreport",
    # Event presets
    "eventpreset",
    # Player onboarding aliases
    "begin", "newplayer",
    # Daily quest aliases
    "dailies", "claimdaily",
    # Daily streak / status
    "streak", "dailystatus",
    # Command check redirect
    "commandcheck",
    # Staff audit log
    "auditlog",
    # Weekly leaderboard
    "weeklylb", "weeklyleaderboard", "weeklyreset",
    "weeklyrewards", "setweeklyreward", "weeklystatus",
    # Party Tip Wallet (public)
    "tip",
    "ptlist", "partytippers",
    "ptlimits", "partytipperlimits",
    "partytipper",
    # Tele / role system
    "tele", "summon", "create", "delete",
    "roles", "rolemembers",
})

LUXE_COMMANDS: frozenset[str] = frozenset({
    # Player-facing
    "tickets", "luxe",
    "luxeshop", "premiumshop",
    "buyticket", "buyluxe",
    "buycoins",
    "use",
    "ticketrate",
    # Luxe auto time
    "autotime", "minetime", "fishtime",
    # Admin
    "luxeadmin",
    "vipadmin",
})

BETA_COMMANDS: frozenset[str] = frozenset({
    # 3.1Q — public
    "testmenu", "betahelp", "quickstart",
    # 3.1Q — admin+
    "betamode", "betacheck", "betadash",
    "staffdash", "stafftools",
    "issueadmin",
    "bugs",
    "staffnote", "staffnotes",
    "permissioncheck", "rolecheck",
    "errors",
    "launchready",
    "announceadmin",
    # 3.1R — beta review + balance audit
    "betatest",
    "topissues",
    "balanceaudit",
    "livebalance",
    "luxebalance",
    "retentionreview",
    "eventreview",
    "seasonreview",
    "funnel",
    "betareport",
    "launchblockers",
    "betastaff",
})

RELEASE_COMMANDS: frozenset[str] = frozenset({
    # Owner mode controls
    "rcmode", "production", "featurefreeze", "economylock", "registrylock",
    # Release info
    "releasenotes", "version", "botversion",
    # Backup / restore
    "backup", "rollbackplan", "restorebackup",
    # Checklists
    "ownerchecklist", "launchownercheck",
    # Announce
    "launchannounce",
    # Public
    "whatsnew", "new", "v32", "knownissues",
    # Dashboards
    "releasedash", "finalaudit",
    # 3.2J — Owner QA + Stable Lock
    "qastatus", "ownerqa", "ownertest",
    "stablecheck", "hotfixpolicy", "stablelock",
})

MONITOR_COMMANDS: frozenset[str] = frozenset({
    # Launch mode
    "launchmode",
    # Post-launch dashboards
    "postlaunch", "livehealth",
    "bugdash", "feedbackdash", "dailyreview",
    # Economy / currency monitors
    "economymonitor", "luxemonitor", "retentionmonitor",
    # Activity monitors
    "eventmonitor", "casinomonitor", "bjmonitor", "pokermonitor",
    # Error dashboard
    "errordash",
    # Hotfix
    "hotfix", "hotfixlog",
    # Launch safety
    "launchlocks", "snapshot",
})

JAIL_COMMANDS: frozenset[str] = frozenset({
    # Player-facing
    "jail", "bail", "jailstatus", "jailtime", "jailhelp",
    # Staff+
    "unjail", "jailrelease", "jailadmin", "jailactive",
    "jailsetcost", "jailsetmax", "jailsetmin", "jailsetbailmultiplier",
    "jailprotectstaff", "jaildebug",
    # Owner setup
    "setjailspot", "setjailguardspot", "setsecurityidle", "setjailreleasespot",
    # Confirm/cancel aliases
    "jailconfirm", "jailcancel",
})

TICKET_ADMIN_COMMANDS: frozenset[str] = frozenset({
    "addtickets", "removetickets", "settickets",
    "sendtickets", "ticketbalance", "ticketlogs", "ticketadmin",
})

ALL_KNOWN_COMMANDS = (
    ALL_KNOWN_COMMANDS
    | TIME_EXP_COMMANDS
    | DISPLAY_COMMANDS
    | FIRSTFIND_COMMANDS
    | NEW_PROJECT_COMMANDS
    | LUXE_COMMANDS
    | BETA_COMMANDS
    | RELEASE_COMMANDS
    | MONITOR_COMMANDS
    | JAIL_COMMANDS
    | TICKET_ADMIN_COMMANDS
)

# Jail staff/admin commands must be in STAFF_CMDS so the on_chat gate lets them through.
# Player-facing jail commands (jail, bail, jailstatus, jailtime, jailhelp,
# jailconfirm, jailcancel) are intentionally kept outside STAFF_CMDS.
JAIL_STAFF_CMDS: frozenset[str] = frozenset({
    "unjail", "jailrelease",
    "jailadmin", "jailactive",
    "jailsetcost", "jailsetmax", "jailsetmin", "jailsetbailmultiplier",
    "jailprotectstaff", "jaildebug",
    "setjailspot", "setjailguardspot", "setsecurityidle", "setjailreleasespot",
})
STAFF_CMDS = STAFF_CMDS | JAIL_STAFF_CMDS
ADMIN_ONLY_CMDS = ADMIN_ONLY_CMDS | {
    "jailadmin", "jailactive",
    "jailsetcost", "jailsetmax", "jailsetmin", "jailsetbailmultiplier",
    "jaildebug",
    "setjailspot", "setjailguardspot", "setsecurityidle", "setjailreleasespot",
}
OWNER_ONLY_CMDS = OWNER_ONLY_CMDS | {"jailprotectstaff"}

# ── Ticket admin permission gates (TICKET_ADMIN_COMMANDS defined above) ──────
STAFF_CMDS       = STAFF_CMDS | TICKET_ADMIN_COMMANDS
ADMIN_ONLY_CMDS  = ADMIN_ONLY_CMDS | {
    "sendtickets", "ticketbalance", "ticketlogs", "ticketadmin",
}
OWNER_ONLY_CMDS  = OWNER_ONLY_CMDS | {
    "addtickets", "removetickets", "settickets",
}

# ── Tip / conversion audit commands (admin+, banker owns) ────────────────
TIP_AUDIT_COMMANDS: frozenset[str] = frozenset({
    "tipaudit", "tipauditdetails", "conversionlogs",
})
ALL_KNOWN_COMMANDS = ALL_KNOWN_COMMANDS | TIP_AUDIT_COMMANDS
STAFF_CMDS         = STAFF_CMDS   | TIP_AUDIT_COMMANDS
ADMIN_ONLY_CMDS    = ADMIN_ONLY_CMDS | TIP_AUDIT_COMMANDS


# ---------------------------------------------------------------------------
# Help texts  (all ≤ 249 chars per whisper)
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "📘 ChillTopia Help\n"
    "New: !start, !guide\n"
    "Progress: !today, !missions\n"
    "Earn: !mine, !fish\n"
    "Play: !bjhelp, !help games"
)

HELP_TEXT_2 = (
    "Shops: !shop, !luxeshop\n"
    "Profile: !profile, !flex\n"
    "Events: !events, !season\n"
    "Use !commands for all."
)

_HELP_CATEGORIES: dict[str, str] = {
    "beginner": (
        "🌱 Beginner Help\n"
        "!start — quick start\n"
        "!tele list — explore\n"
        "!mine / !fish — earn coins\n"
        "!help games — games\n"
        "!shop — shop"
    ),
    "new": (
        "🌱 Beginner Help\n"
        "!start — quick start\n"
        "!tele list — explore\n"
        "!mine / !fish — earn coins\n"
        "!help games — games\n"
        "!shop — shop"
    ),
    "basic": (
        "📌 Basic Commands\n"
        "!balance — check your coins\n"
        "!send [user] [amount] — send coins\n"
        "!profile — view your profile\n"
        "!daily — daily reward\n"
        "!streak — streak status\n"
        "!vip — VIP info\n"
        "!subscribe — notifications\n"
        "!notif — settings"
    ),
    "games": (
        "🎮 Games\n"
        "!mine — mine once\n"
        "!automine — auto mine\n"
        "!fish — fish once\n"
        "!autofish — auto fish\n"
        "!bet [amount] — play blackjack\n"
        "!poker — poker info\n"
        "!coinflip [heads|tails] [amount]\n"
        "!answer [answer] — answer trivia"
    ),
    "blackjack": (
        "🃏 Blackjack\n"
        "!bet [amount] — start\n"
        "!hit — draw\n"
        "!stand — hold\n"
        "!bjstatus — status\n"
        "!balance — coins\n"
        "!bjhelp — more"
    ),
    "poker": (
        "♠️ Poker\n"
        "!poker — start/table\n"
        "!poker join [buyin]\n"
        "!pokerstatus — status\n"
        "!check/!call/!raise/!fold\n"
        "!pokerhelp — more"
    ),
    "mining": (
        "⛏️ Mining\n"
        "!mine — mine once\n"
        "!automine — auto mine\n"
        "!mineinv — top ores  !mineinv all\n"
        "!sellores — sell all ores\n"
        "!mineprofile — your stats\n"
        "!minechances — drop rates\n"
        "!tool — pickaxe  !upgradetool"
    ),
    "fishing": (
        "🎣 Fishing\n"
        "!fish — fish once\n"
        "!autofish — auto fish\n"
        "!fishinv — fish bag  !sellfish\n"
        "!myrod — equipped rod\n"
        "!fishchances — drop rates\n"
        "!fishlist — fish list\n"
        "!rods — rods  !rodshop"
    ),
    "shop": (
        "🛍️ Shop Help\n"
        "Use !shop to view the shop.\n"
        "!shop badges — emoji badges\n"
        "!shop titles — titles\n"
        "!buy [#] — buy shown item\n"
        "!myitems — your items  !balance"
    ),
    "bank": (
        "🏦 Bank Commands\n"
        "!balance — check your coins\n"
        "!send [user] [amount] — send coins\n"
        "!transactions — view recent transactions\n"
        "!bank — bank menu\n"
        "!vip — VIP info\n"
        "!donationgoal — room donation goal"
    ),
    "vip": (
        "💎 VIP Commands\n"
        "!vip — VIP overview & prices\n"
        "!vipperks — view perks\n"
        "!buyvip 1d/7d/30d — buy VIP with coins\n"
        "!myvip — check status & expiry"
    ),
    "support": (
        "💛 Support ChillTopia\n"
        "Ways to support:\n"
        "!vip — VIP perks info\n"
        "!buyvip 1d/7d/30d — buy VIP with coins\n"
        "!donationgoal — room goal progress\n"
        "!topdonors — top supporters\n"
        "VIP gives longer !automine/!autofish sessions."
    ),
    "room": (
        "🏠 Room Commands\n"
        "!tele list — view teleport spots\n"
        "!tele [spot] — teleport to spot\n"
        "!roles — view roles\n"
        "!rolemembers [role] — role members\n"
        "!botspawns — saved bot spots"
    ),
    "party": (
        "🎉 Party Tip\n"
        "During party mode, selected helpers can tip players.\n"
        "View:\n"
        "!ptlist — party tippers\n"
        "!ptlimits — party limits\n"
        "Party Tipper:\n"
        "!tip [user] [amount] — tip one player\n"
        "!tip [players] [amount] — tip random players\n"
        "!tip all [amount] — tip everyone eligible\n"
        "Owner:\n"
        "!pton / !ptoff — party mode on/off\n"
        "!ptwallet [amount] — set wallet\n"
        "!ptadd [user] [mins] — add tipper\n"
        "!ptremove [user] — remove tipper\n"
        "!ptclear — clear all tippers\n"
        "!ptlimit [type] [amount] — set limit"
    ),
    "staff": (
        "🛠️ Staff Commands\n"
        "!mute [user] [mins] [reason]\n"
        "!unmute [user]  !warn [user] [reason]\n"
        "!reports  !roomstatus\n"
        "!bothealth  !modulehealth\n"
        "!botconflicts — check conflicts\n"
        "!summon [user]  !rolemembers [role]"
    ),
    "owner": (
        "👑 Owner Tools\n"
        "!dashboard — owner dashboard\n"
        "!bothealth !modulehealth !botconflicts\n"
        "!stability [on|off|status]\n"
        "!commandissues !checkcommands !checkhelp\n"
        "Fix: !fixbotowners !fixautogames\n"
        "!clearstalebotlocks"
    ),
    "player": (
        "👤 Player Commands\n"
        "!balance  !daily  !profile\n"
        "!notif  !subscribe  !substatus\n"
        "!tele list  !tele [spot]\n"
        "!help games  !help mining  !help fishing"
    ),
    "economy": (
        "💰 Economy\n"
        "!balance — check your coins\n"
        "!send [user] [amount] — send coins\n"
        "!transactions — view history\n"
        "!bank — bank summary\n"
        "!donationgoal — room goal"
    ),
    "notifications": (
        "🔔 Notifications\n"
        "!subscribe — receive alerts\n"
        "!notif — check settings\n"
        "!notifon events — event alerts ON\n"
        "!notifoff events — event alerts OFF\n"
        "!substatus  !unsubscribe"
    ),
    "teleports": (
        "🌀 Teleports\n"
        "!tele list — see spots\n"
        "!tele [spot] — go to spot\n"
        "!summon [user]\n"
        "!roles — role spawns\n"
        "!rolemembers [role] — who's in each role\n"
        "!rolespawns"
    ),
    "events": (
        "🎉 Events\n"
        "!events — schedule\n"
        "!nextevent — next event\n"
        "!eventstatus — status\n"
        "!event — active event\n"
        "!eventhelp  !eventlist"
    ),
    "admin": (
        "⚙️ Admin Commands\n"
        "!adminhelp — full admin reference\n"
        "!economyaudit  !gameprices\n"
        "!setgameprice [game] [setting] [value]\n"
        "!autospawn [on|off|debug [user]]"
    ),
    "casino": (
        "🃏 Casino\n"
        "!bet [amount] — blackjack\n"
        "!hit  !stand  !double  !split\n"
        "!bjhelp — full BJ help\n"
        "!poker join  !call  !fold\n"
        "!pokerhelp — full poker help"
    ),
}

# ---------------------------------------------------------------------------
# Safe help handler — NO DB, NO registry, NO bot-name lookup, NO imports.
# Called as the very first thing in on_chat. Cannot crash the bot.
# ---------------------------------------------------------------------------

_HELP_UNKNOWN_MSG = (
    "Unknown help page.\n"
    "Try: !help basic  !help games  !help bank\n"
    "!help vip  !help mining  !help fishing\n"
    "!help blackjack  !help poker\n"
    "!help room  !help party"
)


async def _handle_safe_help(bot, user, raw_message: str) -> None:
    """
    Crash-proof !help dispatcher.
    Pure static strings — zero external dependencies.
    All bots reply (host-only guard was inside the old dynamic path).
    """
    if BOT_MODE not in ("host", "eventhost", "all"):
        return

    parts = raw_message.strip().split()
    # parts[0] = "!help", parts[1] = optional category
    cat = parts[1].lower() if len(parts) > 1 else ""

    async def _w(msg: str) -> None:
        try:
            await bot.highrise.send_whisper(user.id, str(msg)[:249])
        except Exception:
            pass

    if not cat:
        await _w(HELP_TEXT)
        await _w(HELP_TEXT_2)
        try:
            from modules.permissions import can_moderate, is_owner
            if is_owner(user.username):
                await _w("⚙️ Staff: !help staff  Owner: !help owner  Gold: !goldhelp")
            elif can_moderate(user.username):
                await _w("⚙️ Staff: !help staff  Full: !staffhelp  !adminhelp")
        except Exception:
            pass
        return

    if cat == "staff":
        try:
            from modules.permissions import can_moderate
            if not can_moderate(user.username):
                await _w("Staff help is for staff only.")
                return
        except Exception:
            return

    if cat == "owner":
        try:
            from modules.permissions import is_owner
            if not is_owner(user.username):
                await _w("Owner help is for owners only.")
                return
        except Exception:
            return

    if cat == "room":
        await _w(_HELP_CATEGORIES["room"])
        try:
            from modules.permissions import is_owner as _iown, is_admin as _iadm
            from modules.permissions import is_manager as _imgr
            if _iown(user.username) or _iadm(user.username) or _imgr(user.username):
                await _w(
                    "🏠 Room Setup (Manager+)\n"
                    "!create tele [spot] — save here\n"
                    "!delete tele [spot] — delete it\n"
                    "!summon [user] — bring to you"
                )
                await _w(
                    "🏠 Spawn Setup (Manager+)\n"
                    "!setrolespawn [role] here\n"
                    "!autospawn [on|off|status]\n"
                    "!setbotspawnhere [bot]\n"
                    "!clearbotspawn [bot]"
                )
        except Exception:
            pass
        return

    page = _HELP_CATEGORIES.get(cat)
    if page:
        await _w(page)
    else:
        await _w(_HELP_UNKNOWN_MSG)


GAME_HELP_PAGES = [
    (
        "🎮 Games\n"
        "!trivia  !scramble  !riddle\n"
        "!answer [answer]\n"
        "!coinflip [heads|tails] [bet]\n"
        "Auto games: !autogames status"
    ),
    (
        "🎮 Game Timers\n"
        "Games have answer timers.\n"
        "Staff: !setgametimer [sec]\n"
        "Auto: !autogames on|off"
    ),
]
GAME_HELP = GAME_HELP_PAGES[0]

CASINO_HELP_PAGES = [
    (
        "🎰 Casino\n"
        "Blackjack: !bjoin [bet]  or  !bet [bet]\n"
        "Poker: !poker join\n"
        "Balance: !balance\n"
        "More: !bjhelp  !rbjhelp  !pokerhelp"
    ),
    (
        "🎰 Casino 2\n"
        "!bt table  !bhand — BJ status\n"
        "!blimits  !bstats\n"
        "!pt table  !pokerstats\n"
        "!mycasino — your casino dashboard"
    ),
    (
        "🎰 Casino Settings (Staff)\n"
        "!casinosettings  !casinolimits\n"
        "!casinotoggles\n"
        "Recovery: !bj recover  !rbj recover\n"
        "!poker cleanup  !poker refund"
    ),
]
CASINO_HELP = CASINO_HELP_PAGES[0]

COIN_HELP_PAGES = [
    (
        "💰 Coins\n"
        "!balance — check your balance\n"
        "!balance [user] — check another player\n"
        "!daily — claim daily coins\n"
        "!wallet — coin & casino dashboard\n"
        "!leaderboard — top coin holders"
    ),
    (
        "💰 Coins 2\n"
        "!tiprate — gold-to-coin tip rates\n"
        "!tipstats — your tip history\n"
        "!tipleaderboard — top tippers\n"
        "!coinflip [heads|tails] [bet] — flip a coin"
    ),
]
COIN_HELP = COIN_HELP_PAGES[0]

BANK_HELP_PAGES = [
    (
        "🏦 Bank\n"
        "!send [user] [amount] — send coins\n"
        "!bank — your bank summary\n"
        "!bankstats — transfer stats\n"
        "!transactions — your history\n"
        "!banknotify on|off — send/receive alerts"
    ),
    (
        "🏦 Bank 2\n"
        "!notifications — pending alerts\n"
        "!clearnotifications — clear alerts\n"
        "Staff: !viewtx [user] — view history\n"
        "Staff: !bankwatch [user] — flag player\n"
        "Staff: !banksettings — bank config"
    ),
]
BANK_HELP = BANK_HELP_PAGES[0]

SHOP_HELP_PAGES = [
    (
        "🛒 Shop\n"
        "!shop badges — emoji badge shop\n"
        "!shop titles — title shop\n"
        "!buy [#] — buy shown item by number\n"
        "!shop next — next page\n"
        "!shop prev — previous page\n"
        "!buy badge [id] — buy by ID"
    ),
    (
        "🛒 Shop 2\n"
        "!mybadges — your emoji badges\n"
        "!myitems — all owned items\n"
        "!equip badge [id] — wear a badge\n"
        "!equip title [id] — wear a title\n"
        "!vipstatus — check VIP"
    ),
    (
        "🏷️ Badge Market\n"
        "!badgemarket — player listings\n"
        "!badgebuy [#] — buy by number\n"
        "!badgelist [id] [price] — sell your badge\n"
        "!badgecancel [id] — cancel listing\n"
        "!mybadgelistings — your listings\n"
        "!badgeprices [id] — recent prices"
    ),
]
SHOP_HELP = SHOP_HELP_PAGES[0]

PROFILE_HELP = (
    "👤 Profile\n"
    "!profile [user] [1-6] — view player profile\n"
    "!whois [user] — quick player lookup\n"
    "!me — view your own profile\n"
    "!stats [user] — stats summary\n"
    "!badges [user] — view badges\n"
    "!titles [user] — view titles\n"
    "!privacy [field] on|off — privacy settings\n"
    "!dashboard — full economy overview"
)

PROGRESS_HELP = (
    "🏆 Progress\n"
    "!quests  !dailyquests\n"
    "!weeklyquests\n"
    "!claimquest\n"
    "!achievements\n"
    "!claimachievements"
)

EVENT_HELP_PAGES = [
    (
        "🎉 Events\n"
        "!eventshop — numbered event shop\n"
        "!buy [#] — buy shown event item\n"
        "!eventshop next — next page\n"
        "!eventpoints — your event coins\n"
        "!eventstatus\n"
        "Staff: !startevent [id]"
    ),
    (
        "🎉 Auto Events\n"
        "!autoevents status\n"
        "Staff: !autoevents on|off\n"
        "Staff: !setautoeventinterval [mins]\n"
        "Staff: !setautoeventduration [mins]"
    ),
]

BJ_HELP_PAGES = [
    (
        "🃏 Blackjack Help 1/2\n"
        "Join: !bjoin [bet]  or  !bet [bet]\n"
        "Hit: !bh    Stand: !bs\n"
        "Double: !bd    Split: !bsp\n"
        "Surrender: !bsurrender    Insure: !bi"
    ),
    (
        "🃏 Blackjack Help 2/2\n"
        "Table: !bt    Hand: !bhand\n"
        "Stats: !bstats    Limits: !blimits\n"
        "Rules: !bjrules\n"
        "Balance: !balance"
    ),
]

RBJ_HELP_PAGES = [
    (
        "🃏 Blackjack Help 1/2\n"
        "Join: !bjoin [bet]  or  !bet [bet]\n"
        "Hit: !bh    Stand: !bs\n"
        "Double: !bd    Split: !bsp\n"
        "Surrender: !bsurrender    Insure: !bi"
    ),
    (
        "🃏 Blackjack Help 2/2\n"
        "Table: !bt    Hand: !bhand\n"
        "Stats: !bstats    Limits: !blimits\n"
        "Rules: !bjrules\n"
        "Balance: !balance"
    ),
]

CASINO_ADMIN_HELP_PAGES = [
    (
        "🎰 Casino Admin 1\n"
        "!casinosettings\n"
        "!casinolimits\n"
        "!casinotoggles\n"
        "!bj on|off\n"
        "!rbj on|off\n"
        "!poker on|off"
    ),
    (
        "🎰 Casino Admin 2\n"
        "!bj winlimit on|off\n"
        "!bj losslimit on|off\n"
        "!rbj winlimit on|off\n"
        "!rbj losslimit on|off\n"
        "!resetbjlimits [user]\n"
        "!resetrbjlimits [user]"
    ),
    (
        "🎰 Casino Admin 2b\n"
        "!setbjlimits [min] [max] [win] [loss]\n"
        "!setrbjlimits [min] [max] [win] [loss]\n"
        "!setbjactiontimer [sec]\n"
        "!setrbjactiontimer [sec]"
    ),
    (
        "🎰 Casino Admin 2c\n"
        "!bj double on|off\n"
        "!rbj double on|off\n"
        "!bj split on|off\n"
        "!rbj split on|off\n"
        "!setbjmaxsplits [n]\n"
        "!setrbjmaxsplits [n]"
    ),
    (
        "🎰 Casino Admin 3\n"
        "!bj state\n"
        "!rbj state\n"
        "!bj recover\n"
        "!rbj recover\n"
        "!bj refund\n"
        "!rbj refund"
    ),
    (
        "🎰 Casino Admin 4\n"
        "!bj forcefinish\n"
        "!rbj forcefinish\n"
        "!poker cancel  !poker refund\n"
        "!poker forcefinish\n"
        "!casino reset"
    ),
    (
        "🎰 Casino Admin 5 — Poker\n"
        "!setpokerbuyin [min] [max]\n"
        "!setpokerplayers [min] [max]\n"
        "!setpokertimer [sec]\n"
        "!setpokerraise [min] [max]"
    ),
    (
        "🎰 Casino Admin 6 — Poker\n"
        "!setpokerdailywinlimit [amount]\n"
        "!setpokerdailylosslimit [amount]\n"
        "!poker winlimit on|off\n"
        "!poker losslimit on|off\n"
        "!resetpokerlimits [user]"
    ),
]

BANK_ADMIN_HELP_PAGES = [
    (
        "🏦 Bank Staff 1\n"
        "!viewtx [user]\n"
        "!bankwatch [user]\n"
        "!ledger [user]\n"
        "!auditbank [user]"
    ),
    (
        "🏦 Bank Admin 2\n"
        "!bankblock [user]\n"
        "!bankunblock [user]\n"
        "!banksettings\n"
        "!setsendlimit [amount]"
    ),
    (
        "🏦 Bank Admin 3\n"
        "!setminsend [amount]\n"
        "!setmaxsend [amount]\n"
        "!setnewaccountdays [days]\n"
        "!setminlevelsend [level]"
    ),
    (
        "🏦 Bank Admin 4\n"
        "!setmintotalearned [amount]\n"
        "!setmindailyclaims [amount]\n"
        "!setsendtax [percent]\n"
        "!sethighriskblocks on|off"
    ),
]

REP_HELP = (
    "⭐ Reputation\n"
    "!rep [user]\n"
    "!reputation\n"
    "!repstats\n"
    "!toprep\n"
    "!repleaderboard"
)

AUTO_HELP = (
    "🤖 Auto Systems\n"
    "!autogames status\n"
    "!autoevents status\n"
    "!gameconfig\n"
    "Staff can enable/disable."
)

VIP_HELP_PAGES = [
    (
        "💎 VIP\n"
        "!vip — view VIP info\n"
        "!vipperks — see VIP perks\n"
        "!buyvip [1d|7d|30d] — purchase VIP\n"
        "!myvip — check your VIP status\n"
        "!donationgoal — room goal\n"
        "!topdonors — top supporters"
    ),
    (
        "💎 VIP Staff\n"
        "!grantvip [user] [days] — grant VIP\n"
        "!removevip [user] — revoke VIP\n"
        "!setvipprice [1d|7d|30d] [amount]\n"
        "!vips — list all VIP players"
    ),
]

TIP_HELP = (
    "💰 Tips\n"
    "Tip the bot gold to get coins.\n"
    "!tiprate\n"
    "!tipstats\n"
    "!tipleaderboard\n"
    "Min tip reward: 10g"
)

ROLES_HELP = (
    "Roles\n"
    "Owner: all\n"
    "Admin: economy/staff\n"
    "Manager: games/events\n"
    "Mod: reports/reset\n"
    "Use !allstaff"
)

AUDIT_HELP_TEXT = (
    "🔍 Audit\n"
    "!audit [user]\n"
    "!auditbank [user]\n"
    "!auditcasino [user]\n"
    "!auditeconomy [user]\n"
    "!ledger [user]"
)

REPORT_HELP_PAGES = [
    (
        "🚩 Reports\n"
        "!report [user] [reason]\n"
        "!bug [message]\n"
        "!myreports"
    ),
    (
        "🚩 Staff Reports\n"
        "!reports\n"
        "!reportinfo [id]\n"
        "!closereport [id]\n"
        "!reportwatch [user]"
    ),
]

MAINTENANCE_HELP_TEXT = (
    "🛠️ Maintenance\n"
    "!botstatus\n"
    "!dbstats\n"
    "!backup\n"
    "!maintenance on|off\n"
    "!reloadsettings\n"
    "!cleanup\n"
    "!softrestart"
)

# ── Staff help texts ──────────────────────────────────────────────────────────

STAFF_HELP_TEXT = (
    "⚙️ Staff Help Index\n"
    "!control - control center\n"
    "!modhelp - moderation commands\n"
    "!managerhelp - game & event tools\n"
    "!adminhelp - economy & admin power\n"
    "!ownerhelp - owner commands"
)

STAFF_HELP_TEXT_2 = (
    "⚙️ Staff Help 2\n"
    "!mycommands - commands for your role\n"
    "!adminpanel - admin control panel\n"
    "!adminlogs - action log\n"
    "!status - bot status\n"
    "!quicktoggles - toggle modules\n"
    "!audithelp !reporthelp - audit tools"
)

MOD_HELP_PAGES = [
    (
        "🔨 Mod 1 — Reports\n"
        "!reports — view open reports\n"
        "!reportinfo [id] — report details\n"
        "!closereport [id] — close a report\n"
        "!reportwatch [user] — flag a player\n"
        "!myreports — view your reports"
    ),
    (
        "🔨 Mod 2 — Moderation\n"
        "!warn [user] [reason] — issue a warning\n"
        "!warnings [user] — view warnings\n"
        "!mute [user] [mins] — bot-mute a player\n"
        "!unmute [user] — remove mute\n"
        "!mutes — list active mutes"
    ),
    (
        "🔨 Mod 3 — Audit\n"
        "!viewtx [user] — transaction history\n"
        "!bankwatch [user] — flag bank activity\n"
        "!ledger [user] — full ledger\n"
        "!audit [user] — full audit trail"
    ),
    (
        "🔨 Mod 4 — Tools\n"
        "!announce [msg] — room announcement\n"
        "!resetgame — clear stuck games\n"
        "!casino reset — reset casino\n"
        "!rules — show room rules\n"
        "!dailyadmin reports — daily report"
    ),
]

MANAGER_HELP_PAGES = [
    (
        "🧰 Manager 1 — Control\n"
        "!control — control center\n"
        "!control room — room tools\n"
        "!control games — mining/events\n"
        "!control casino — casino panel\n"
        "!quicktoggles — toggle modules"
    ),
    (
        "🧰 Manager 2 — Events\n"
        "!startevent [id] — start an event\n"
        "!stopevent — stop current event\n"
        "!autogames on|off — toggle auto games\n"
        "!autoevents on|off — toggle auto events\n"
        "!gameconfig — game configuration"
    ),
    (
        "🧰 Manager 3 — Casino\n"
        "!bj on|off — toggle blackjack\n"
        "!rbj on|off — toggle realistic BJ\n"
        "!casino reset — reset all games\n"
        "!casinosettings — view settings\n"
        "!casinotoggles — toggle features"
    ),
    (
        "🧰 Manager 4 — BJ Settings\n"
        "!setbjlimits [min] [max] [win] [loss]\n"
        "!setrbjlimits [min] [max] [win] [loss]\n"
        "!setbjactiontimer [sec]\n"
        "!setrbjactiontimer [sec]\n"
        "!bj settings — view BJ settings"
    ),
    (
        "🧰 Manager 5 — Poker\n"
        "!setpokerbuyin [min] [max]\n"
        "!setpokertimer [sec] — turn timer\n"
        "!setpokerraise [min] [max]\n"
        "!poker state — table status\n"
        "!healthcheck — bot health"
    ),
    (
        "🧰 Manager 6 — Reports\n"
        "!dailyadmin — full daily report\n"
        "!dailyadmin casino — casino stats\n"
        "!dailyadmin events — event stats\n"
        "!dailyadmin bank — bank stats\n"
        "!automod on|off — auto moderation"
    ),
]

ADMIN_HELP_PAGES = [
    (
        "🛡️ Admin 0 — Control\n"
        "!control — control center\n"
        "!control economy — economy panel\n"
        "!control casino — casino panel\n"
        "!control shop — shop panel\n"
        "!control system — system panel"
    ),
    (
        "🛡️ Admin 1 — Economy\n"
        "!addcoins [user] [amount]\n"
        "!removecoins [user] [amount]\n"
        "!setcoins [user] [amount]\n"
        "!editcoins [user] [amount]\n"
        "!resetcoins [user]"
    ),
    (
        "🛡️ Admin 2 — XP & Level\n"
        "!addxp [user] [amount]\n"
        "!removexp [user] [amount]\n"
        "!setxp [user] [amount]\n"
        "!setlevel [user] [level]\n"
        "!addlevel [user] [amount]\n"
        "!removelevel [user] [amount]"
    ),
    (
        "🛡️ Admin 3 — Rep & Events\n"
        "!addrep [user] [amount]\n"
        "!removerep [user] [amount]\n"
        "!setrep [user] [amount]\n"
        "!resetrep [user]\n"
        "!addeventcoins [user] [amount]\n"
        "!seteventcoins [user] [amount]"
    ),
    (
        "🛡️ Admin 4 — Items & VIP\n"
        "!givetitle [user] [id]\n"
        "!settitle [user] [id]\n"
        "!givebadge [user] [id]\n"
        "!setbadge [user] [id]\n"
        "!grantvip [user] [days]\n"
        "!setvipprice [amount]"
    ),
    (
        "🛡️ Admin 5 — Roles\n"
        "!addmanager [user]\n"
        "!removemanager [user]\n"
        "!addmoderator [user]\n"
        "!removemoderator [user]\n"
        "!allstaff — list all staff"
    ),
    (
        "🛡️ Admin 6 — Casino\n"
        "!resetbjstats [user]\n"
        "!resetrbjstats [user]\n"
        "!resetpokerstats [user]\n"
        "!resetcasinostats [user]\n"
        "!resetbjlimits [user]"
    ),
    (
        "🛡️ Admin 7 — System\n"
        "!adminlogs [user] — action log\n"
        "!adminloginfo [id] — log detail\n"
        "!adminpanel — control panel\n"
        "!dbstats — database stats\n"
        "!maintenance on|off\n"
        "!bankblock [user]"
    ),
    (
        "🛡️ Admin 8 — Cmd Audit\n"
        "!checkcommands — audit routes\n"
        "!checkhelp — audit help menus\n"
        "!missingcommands — unrouted cmds\n"
        "!routecheck — unlisted routes\n"
        "!silentcheck — silent risk cmds\n"
        "!commandtest [cmd] — test a route\n"
        "!currencycheck — currency scan"
    ),
]

OWNER_HELP_PAGES = [
    (
        "👑 Owner 0 — Control\n"
        "!control — full control center\n"
        "!ownerpanel — owner hub\n"
        "!control staff 3 — owner roles\n"
        "!control system 3 — owner system\n"
        "!quicktoggles — toggle modules"
    ),
    (
        "👑 Owner 1 — Roles\n"
        "!addowner [user] — add an owner\n"
        "!removeowner [user] — remove owner\n"
        "!owners — list owners\n"
        "!addadmin [user] — promote to admin\n"
        "!removeadmin [user] — demote admin"
    ),
    (
        "👑 Owner 2 — Economy\n"
        "!setcoins [user] [amount]\n"
        "!addeventcoins [user] [amount]\n"
        "!setlevel [user] [level]\n"
        "!setrep [user] [amount]\n"
        "!givetitle [user] [id]"
    ),
    (
        "👑 Owner 3 — Items & VIP\n"
        "!givebadge [user] [id]\n"
        "!grantvip [user] [days]\n"
        "!resetcasinostats [user]\n"
        "!adminlogs [user] — action log\n"
        "!adminpanel — control panel"
    ),
    (
        "👑 Owner 4 — Gold\n"
        "!goldtip [user] [amount]\n"
        "!goldtip all [amount]\n"
        "!goldrain [amount] [winners]\n"
        "!goldrefund [user]\n"
        "!goldrainvip [amount]"
    ),
    (
        "👑 Owner 5B — Party Tip\n"
        "!pton — party mode on\n"
        "!ptoff — party mode off\n"
        "!ptwallet [amount] — set party wallet\n"
        "!ptadd [user] [mins] — add tipper\n"
        "!ptremove [user] — remove tipper\n"
        "!ptclear  !ptlimit [type] [amount]"
    ),
    (
        "👑 Owner 5 — System\n"
        "!allstaff — list all staff\n"
        "!allcommands — full command list\n"
        "!backup — backup database\n"
        "!softrestart — reload bot\n"
        "!restartbot — full restart\n"
        "!checkhelp — help system check"
    ),
    (
        "👑 Owner 6 — Cmd Audit\n"
        "!checkcommands — audit routes\n"
        "!checkhelp — audit help menus\n"
        "!missingcommands — unrouted cmds\n"
        "!routecheck — unlisted routes\n"
        "!silentcheck — silent risk cmds\n"
        "!commandtest [cmd] — test a route\n"
        "!currencycheck — currency scan"
    ),
    (
        "👑 Owner 7 — Recovery\n"
        "!poker recoverystatus\n"
        "!poker state — poker state info\n"
        "!poker cleanup — cleanup stuck hand\n"
        "!casinointegrity full\n"
        "!poker closeforce — emergency close"
    ),
]

ALLCMDS = [
    "Cmds 1 Help\n!help  !gamehelp  !casinohelp\n!coinhelp  !bankhelp\n!shophelp  !profilehelp",
    "Cmds 2 Games\n!trivia  !scramble  !riddle\n!answer  !coinflip\n!autogames status\n!gameconfig",
    "Cmds 3 Casino\n!bet [amount]  !hit  !stand  !double\n!split  !insurance  !surrender\n!bj rules|stats|shoe|table",
    "Cmds 4 Casino Staff\n!casinosettings\n!casinolimits\n!casinotoggles\n!setbjlimits\n!setrbjlimits\n!setbjactiontimer\n!setrbjactiontimer",
    "Cmds 5 Bank\n!send  !bank  !bankstats\n!transactions\n!banknotify\n!tiprate  !tipstats",
    "Cmds 6 Shop/Profile\n!shop titles|badges\n!titleinfo  !badgeinfo\n!buy  !equip\n!profile  !level",
    "Cmds 7 Progress\n!quests  !dailyquests\n!weeklyquests\n!claimquest\n!achievements\n!reputation",
    "Cmds 8 Events\n!event  !events\n!eventstatus\n!startevent\n!stopevent\n!autoevents status",
    "Cmds 9 Staff\n!staffhelp !modhelp\n!managerhelp !adminhelp\n!ownerhelp !allstaff",
    "Cmds 10 Gold/Owner\n!goldhelp\n!goldtip\n!goldrain\n!goldrefund\n!backup\n!softrestart",
    "Cmds 11 Party Tip\n!party on|off\n!ptwallet [amt]\n!ptadd [u] [m]\n!ptlist\n!ptlimits\n!ptlimit",
]


# ---------------------------------------------------------------------------
# Help audit page builder — used by !helpaudit / !messageaudit help
# ---------------------------------------------------------------------------

def _build_audit_help_pages() -> list[tuple[str, str]]:
    """Collect every help text string into (label, text) pairs for scanning."""
    pages: list[tuple[str, str]] = []

    pages.append(("help",  HELP_TEXT))
    pages.append(("help2", HELP_TEXT_2))

    for cat, text in _HELP_CATEGORIES.items():
        pages.append((f"help:{cat}", text))

    for i, p in enumerate(BJ_HELP_PAGES):
        pages.append((f"bj p{i+1}", p))
    for i, p in enumerate(RBJ_HELP_PAGES):
        pages.append((f"rbj p{i+1}", p))
    for i, p in enumerate(MINE_HELP_PAGES):
        pages.append((f"mine p{i+1}", p))
    for i, p in enumerate(POKER_HELP_PAGES):
        pages.append((f"poker p{i+1}", p))
    for i, p in enumerate(CASINO_ADMIN_HELP_PAGES):
        pages.append((f"casino_admin p{i+1}", p))
    for i, p in enumerate(BANK_ADMIN_HELP_PAGES):
        pages.append((f"bank_admin p{i+1}", p))
    for i, p in enumerate(VIP_HELP_PAGES):
        pages.append((f"vip p{i+1}", p))
    for i, p in enumerate(REPORT_HELP_PAGES):
        pages.append((f"report p{i+1}", p))
    for i, p in enumerate(MOD_HELP_PAGES):
        pages.append((f"mod p{i+1}", p))
    for i, p in enumerate(MANAGER_HELP_PAGES):
        pages.append((f"manager p{i+1}", p))
    for i, p in enumerate(ADMIN_HELP_PAGES):
        pages.append((f"admin p{i+1}", p))
    for i, p in enumerate(OWNER_HELP_PAGES):
        pages.append((f"owner p{i+1}", p))

    pages.append(("staff_help",  STAFF_HELP_TEXT))
    pages.append(("staff_help2", STAFF_HELP_TEXT_2))
    pages.append(("tip_help",    TIP_HELP))
    pages.append(("rep_help",    REP_HELP))
    pages.append(("auto_help",   AUTO_HELP))
    pages.append(("audit_help",  AUDIT_HELP_TEXT))
    pages.append(("maint_help",  MAINTENANCE_HELP_TEXT))

    for i, p in enumerate(ALLCMDS):
        pages.append((f"allcmds p{i+1}", p))

    return pages


# ---------------------------------------------------------------------------
# Module-level helpers for casino and manager commands
# ---------------------------------------------------------------------------

# Pending /casino reset confirmations: {user_id: {"code": str, "ts": float}}
_pending_casino_reset: dict = {}
# Per-user cooldown for wrong-bot whisper routing hints (timestamp of last hint)
_whisper_wrong_bot_ts: dict[str, float] = {}


async def _handle_casino_cmd(bot, user, args):
    sub = args[1].lower() if len(args) > 1 else ""
    if sub == "modes":
        s_bj  = db.get_bj_settings()
        s_rbj = db.get_rbj_settings()
        bj_on  = "ON"  if int(s_bj.get("bj_enabled",  1)) else "OFF"
        rbj_on = "ON"  if int(s_rbj.get("rbj_enabled", 1)) else "OFF"
        await bot.highrise.send_whisper(
            user.id,
            f"🎰 Modes: Casual BJ {bj_on} | Realistic BJ {rbj_on}"
        )
    elif sub == "on":
        if not can_manage_games(user.username):
            await bot.highrise.send_whisper(user.id, "Admins and managers only.")
            return
        db.set_bj_setting("bj_enabled", 1)
        db.set_rbj_setting("rbj_enabled", 1)
        await bot.highrise.chat("✅ Casino is now OPEN. Both BJ modes enabled.")
    elif sub == "off":
        if not can_manage_games(user.username):
            await bot.highrise.send_whisper(user.id, "Admins and managers only.")
            return
        db.set_bj_setting("bj_enabled", 0)
        db.set_rbj_setting("rbj_enabled", 0)
        await bot.highrise.chat("⛔ Casino is now CLOSED. Both BJ modes disabled.")

    elif sub == "reset":
        if not can_moderate(user.username):
            await bot.highrise.send_whisper(user.id, "Staff only.")
            return
        # If either BJ or RBJ has an active table, require confirmation
        from modules.blackjack           import _state as _bj_state
        from modules.realistic_blackjack import _state as _rbj_state
        bj_active  = _bj_state.phase  != "idle"
        rbj_active = _rbj_state.phase != "idle"
        if bj_active or rbj_active:
            import random, string
            code = "".join(random.choices(string.digits, k=4))
            _pending_casino_reset[user.id] = {
                "code": code,
                "ts":   asyncio.get_event_loop().time(),
            }
            tables = []
            if bj_active:
                tables.append(f"BJ ({_bj_state.phase})")
            if rbj_active:
                tables.append(f"RBJ ({_rbj_state.phase})")
            await bot.highrise.send_whisper(
                user.id,
                f"⚠️ Active tables: {', '.join(tables)}.\n"
                f"To confirm reset+refund, type:\n"
                f"!confirmcasinoreset {code}\n"
                "(expires in 60s)"
            )
            return
        bj_reset_table()
        rbj_reset_table()
        poker_reset_table()
        await bot.highrise.chat("✅ Casino tables reset (BJ, RBJ, Poker).")

    elif sub == "leaderboard":
        await _send_casino_leaderboard(bot, user)

    else:
        await bot.highrise.send_whisper(
            user.id,
            "Usage: !casino modes | !casino on | !casino off\n"
            "!casino reset | !casino leaderboard"
        )


async def _send_casino_leaderboard(bot, user):
    """Send BJ then RBJ top-5 leaderboards as two whispers."""
    for rows, header in [
        (db.get_bj_leaderboard(),  "-- BJ Top 5 (Net Profit) --"),
        (db.get_rbj_leaderboard(), "-- RBJ Top 5 (Net Profit) --"),
    ]:
        if not rows:
            await bot.highrise.send_whisper(user.id, f"{header}\nNo data yet.")
        else:
            lines = [header]
            for i, r in enumerate(rows, 1):
                name = db.get_display_name(r["user_id"], r["username"])
                net  = r["net"]
                sign = "+" if net >= 0 else ""
                lines.append(f"{i}. {name}  {sign}{net:,} 🪙")
            await bot.highrise.send_whisper(user.id, "\n".join(lines))


async def _handle_staffhelp(bot, user):
    if not can_moderate(user.username):
        await bot.highrise.send_whisper(user.id, "Staff help is for staff only.")
        return
    await bot.highrise.send_whisper(user.id, STAFF_HELP_TEXT)
    await bot.highrise.send_whisper(user.id, STAFF_HELP_TEXT_2)


def _paged_send(pages):
    """Return a helper coroutine that whispers one or all pages."""
    async def _inner(bot, user, args):
        page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
        n = len(pages)
        if page == 0:
            for p in pages:
                await bot.highrise.send_whisper(user.id, p)
        elif 1 <= page <= n:
            await bot.highrise.send_whisper(user.id, pages[page - 1])
        else:
            await bot.highrise.send_whisper(user.id, f"Pages 1-{n}.")
    return _inner


_handle_gamehelp        = _paged_send(GAME_HELP_PAGES)
_handle_casinohelp      = _paged_send(CASINO_HELP_PAGES)
_handle_bankhelp        = _paged_send(BANK_HELP_PAGES)
_handle_shophelp        = _paged_send(SHOP_HELP_PAGES)
_handle_coinhelp        = _paged_send(COIN_HELP_PAGES)
_handle_eventhelp_paged = _paged_send(EVENT_HELP_PAGES)
_handle_bjhelp          = _paged_send(BJ_HELP_PAGES)
_handle_rbjhelp         = _paged_send(RBJ_HELP_PAGES)


async def _handle_bankerhelp(bot, user, _args):
    await bot.highrise.send_whisper(
        user.id,
        "🏦 Banker: !balance  !coinhelp  !bankhelp"
    )


async def _handle_howtoplay(bot, user, _args=None):
    """How-to-play / game guide — whispered to caller."""
    lines = [
        "🎮 Games: Blackjack → !bjoin [bet]  Poker → !poker join",
        "🃏 Blackjack: !bh hit  !bs stand  !bd double  !bsp split  !bjhelp",
        "♠️ Poker: !call  !raise [amount]  !fold  !check  !pokerhelp",
        "💰 Economy: !daily  !balance  !shop  !mine  !fish  !help",
    ]
    for line in lines:
        await bot.highrise.send_whisper(user.id, line[:249])


async def _handle_economydbcheck(bot, user):
    print(f"[RX] mode={BOT_MODE} text=/economydbcheck")
    print(f"[ROUTE] /economydbcheck owner=banker current={BOT_MODE} handle=true")
    if not can_manage_economy(user.username):
        await bot.highrise.send_whisper(user.id, "Admin only.")
        return
    try:
        import sqlite3 as _sql
        _path = config.SHARED_DB_PATH or "highrise_hangout.db"
        with _sql.connect(_path) as _conn:
            _cur = _conn.cursor()
            _issues: list[str] = []
            for _tbl in ("users", "economy_settings", "bank_user_stats",
                         "bank_transactions", "daily_claims", "ledger"):
                _cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (_tbl,),
                )
                if not _cur.fetchone():
                    _issues.append(f"missing:{_tbl}")
            if not _issues:
                _cur.execute("PRAGMA table_info(users)")
                _cols = {r[1] for r in _cur.fetchall()}
                for _col in ("user_id", "username", "balance"):
                    if _col not in _cols:
                        _issues.append(f"missing_col:users.{_col}")
        _msg = ("Economy DB: OK" if not _issues
                else "Economy DB fail: " + ", ".join(_issues[:3]))
    except Exception as _exc:
        _msg = f"Economy DB error: {str(_exc)[:80]}"
    print(f"[ECONOMYDBCHECK] @{user.username}: {_msg}")
    await bot.highrise.send_whisper(user.id, _msg[:249])


async def _handle_economyrepair(bot, user):
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Owner only.")
        return
    try:
        import database as _db_mod
        _db_mod.init_db()
        _msg = "✅ Economy DB repaired."
    except Exception as _exc:
        _msg = f"Economy repair error: {str(_exc)[:80]}"
    print(f"[ECONOMYREPAIR] @{user.username}: {_msg}")
    await bot.highrise.send_whisper(user.id, _msg[:249])


async def _handle_casinoadminhelp(bot, user, args):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    await _paged_send(CASINO_ADMIN_HELP_PAGES)(bot, user, args)


async def _handle_bankadminhelp(bot, user, args):
    if not can_moderate(user.username):
        await bot.highrise.send_whisper(user.id, "Staff only.")
        return
    is_admin_plus = can_manage_economy(user.username)
    if not is_admin_plus:
        await bot.highrise.send_whisper(user.id, BANK_ADMIN_HELP_PAGES[0])
        return
    await _paged_send(BANK_ADMIN_HELP_PAGES)(bot, user, args)


async def _handle_maintenancehelp(bot, user):
    if not can_manage_economy(user.username):
        await bot.highrise.send_whisper(user.id, "Maintenance help is admin only.")
        return
    await bot.highrise.send_whisper(user.id, MAINTENANCE_HELP_TEXT)


async def _handle_viphelp(bot, user, args):
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    n = len(VIP_HELP_PAGES)
    if page == 0:
        await bot.highrise.send_whisper(user.id, VIP_HELP_PAGES[0])
        if can_moderate(user.username):
            await bot.highrise.send_whisper(user.id, VIP_HELP_PAGES[1])
    elif 1 <= page <= n:
        if page == 2 and not can_moderate(user.username):
            await bot.highrise.send_whisper(user.id, "Staff only.")
        else:
            await bot.highrise.send_whisper(user.id, VIP_HELP_PAGES[page - 1])
    else:
        await bot.highrise.send_whisper(user.id, f"Pages 1-{n}.")


async def _handle_tiphelp(bot, user):
    await bot.highrise.send_whisper(user.id, TIP_HELP)


async def _handle_rephelp(bot, user):
    await bot.highrise.send_whisper(user.id, REP_HELP)


async def _handle_autohelp(bot, user):
    await bot.highrise.send_whisper(user.id, AUTO_HELP)


async def _handle_roleshelp(bot, user):
    await bot.highrise.send_whisper(user.id, ROLES_HELP)


async def _handle_audithelp_cmd(bot, user):
    if not can_moderate(user.username):
        await bot.highrise.send_whisper(user.id, "Staff only.")
        return
    await bot.highrise.send_whisper(user.id, AUDIT_HELP_TEXT)


async def _handle_reporthelp_cmd(bot, user, args):
    await bot.highrise.send_whisper(user.id, REPORT_HELP_PAGES[0])
    if can_moderate(user.username):
        await bot.highrise.send_whisper(user.id, REPORT_HELP_PAGES[1])


async def _handle_modhelp(bot, user, args):
    if not can_moderate(user.username):
        await bot.highrise.send_whisper(user.id, "Staff only.")
        return
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    n = len(MOD_HELP_PAGES)
    if page == 0:
        # Send all pages
        for p in MOD_HELP_PAGES:
            await bot.highrise.send_whisper(user.id, p)
    elif 1 <= page <= n:
        await bot.highrise.send_whisper(user.id, MOD_HELP_PAGES[page - 1])
    else:
        await bot.highrise.send_whisper(user.id, f"Mod help pages 1-{n}.")


async def _handle_managerhelp(bot, user, args):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    n = len(MANAGER_HELP_PAGES)
    if page == 0:
        for p in MANAGER_HELP_PAGES:
            await bot.highrise.send_whisper(user.id, p)
    elif 1 <= page <= n:
        await bot.highrise.send_whisper(user.id, MANAGER_HELP_PAGES[page - 1])
    else:
        await bot.highrise.send_whisper(user.id, f"Manager help pages 1-{n}.")


async def _handle_adminhelp(bot, user, args):
    if not can_manage_economy(user.username):
        await bot.highrise.send_whisper(user.id, "Admins and owners only.")
        return
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    n = len(ADMIN_HELP_PAGES)
    if page == 0:
        for p in ADMIN_HELP_PAGES:
            await bot.highrise.send_whisper(user.id, p)
    elif 1 <= page <= n:
        await bot.highrise.send_whisper(user.id, ADMIN_HELP_PAGES[page - 1])
    else:
        await bot.highrise.send_whisper(user.id, f"Admin help pages 1-{n}.")


async def _handle_ownerhelp(bot, user, args):
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Owner only.")
        return
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    n = len(OWNER_HELP_PAGES)
    if page == 0:
        for p in OWNER_HELP_PAGES:
            await bot.highrise.send_whisper(user.id, p)
    elif 1 <= page <= n:
        await bot.highrise.send_whisper(user.id, OWNER_HELP_PAGES[page - 1])
    else:
        await bot.highrise.send_whisper(user.id, f"Owner help pages 1-{n}.")


async def _handle_staff_cmd(bot, user, cmd, args):
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, f"Usage: !{cmd} <username>")
        return
    target = args[1].lstrip("@").lower()
    if cmd == "addowner":
        r = db.add_owner_user(target)
        msg = (f"@{target} is already an owner."
               if r == "exists" else f"👑 @{target} is now an owner.")
    elif cmd == "removeowner":
        r = db.remove_owner_user(target)
        if r == "last_owner":
            msg = "Cannot remove the final owner."
        elif r == "not_found":
            msg = f"@{target} is not an owner."
        else:
            msg = f"❌ @{target} is no longer an owner."
    elif cmd == "addmanager":
        r = db.add_manager(target)
        msg = (f"@{target} is already a manager."
               if r == "exists" else f"✅ @{target} is now a manager.")
    elif cmd == "removemanager":
        r = db.remove_manager(target)
        msg = (f"@{target} is not a manager."
               if r == "not_found" else f"❌ @{target} removed as manager.")
    elif cmd == "addmoderator":
        r = db.add_moderator(target)
        msg = (f"@{target} is already a moderator."
               if r == "exists" else f"✅ @{target} is now a moderator.")
    elif cmd == "removemoderator":
        r = db.remove_moderator(target)
        msg = (f"@{target} is not a moderator."
               if r == "not_found" else f"❌ @{target} removed as moderator.")
    elif cmd == "addadmin":
        r = db.add_admin_user(target)
        msg = (f"@{target} is already an admin."
               if r == "exists" else f"✅ @{target} is now an admin.")
    elif cmd == "removeadmin":
        r = db.remove_admin_user(target)
        msg = (f"@{target} is not an admin."
               if r == "not_found" else f"❌ @{target} removed as admin.")
    else:
        return
    await bot.highrise.send_whisper(user.id, msg)


async def _cmd_owners(bot, user):
    cfg_owners = [u.lower() for u in config.OWNER_USERS]
    db_owners  = db.get_owner_users()
    all_owners = sorted(set(cfg_owners + db_owners))
    msg = "Owners: " + ", ".join(f"@{o}" for o in all_owners) if all_owners else "No owners set."
    await bot.highrise.send_whisper(user.id, msg[:245])


async def _cmd_admins(bot, user):
    owner_set     = {u.lower() for u in config.OWNER_USERS}
    config_admins = [u.lower() for u in config.ADMIN_USERS if u.lower() not in owner_set]
    dynamic       = db.get_admin_users()
    all_admins    = sorted(set(config_admins + dynamic))
    msg = "Admins: " + ", ".join(f"@{a}" for a in all_admins) if all_admins else "No admins set."
    await bot.highrise.send_whisper(user.id, msg[:245])


async def _cmd_allstaff(bot, user, args):
    """Show all staff grouped by role, with optional filter/page."""
    sub = args[1].lower() if len(args) > 1 else ""

    cfg_owners  = [u.lower() for u in config.OWNER_USERS]
    db_owners   = db.get_owner_users()
    owners      = sorted(set(cfg_owners + db_owners))

    cfg_admins  = [u.lower() for u in config.ADMIN_USERS
                   if u.lower() not in {o.lower() for o in owners}]
    db_admins   = db.get_admin_users()
    admins      = sorted(set(cfg_admins + db_admins))

    managers    = db.get_managers()
    moderators  = db.get_moderators()

    def fmt(label, emoji, names):
        line = f"{emoji} {label}: " + (", ".join(f"@{n}" for n in names) if names else "None")
        return line[:245]

    if sub in ("owners", "1"):
        await bot.highrise.send_whisper(user.id, fmt("Owners", "👑", owners))
    elif sub in ("admins", "2"):
        await bot.highrise.send_whisper(user.id, fmt("Admins", "🛡️", admins))
    elif sub in ("managers", "3"):
        await bot.highrise.send_whisper(user.id, fmt("Managers", "🧰", managers))
    elif sub in ("moderators", "mods", "4"):
        await bot.highrise.send_whisper(user.id, fmt("Mods", "🔨", moderators))
    else:
        # Show all sections (each as a separate whisper to stay under 249 chars)
        await bot.highrise.send_whisper(user.id, fmt("Owners",   "👑", owners))
        await bot.highrise.send_whisper(user.id, fmt("Admins",   "🛡️", admins))
        await bot.highrise.send_whisper(user.id, fmt("Managers", "🧰", managers))
        await bot.highrise.send_whisper(user.id, fmt("Mods",     "🔨", moderators))


async def _cmd_allcommands(bot, user, args):
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    n = len(ALLCMDS)
    if page == 0:
        await bot.highrise.send_whisper(user.id, f"Use !allcommands 1-{n}.")
    elif 1 <= page <= n:
        await bot.highrise.send_whisper(user.id, ALLCMDS[page - 1])
    else:
        await bot.highrise.send_whisper(user.id, f"Use !allcommands 1-{n}.")


async def _cmd_checkcommands(bot, user):
    """Show which command modules are installed and routed."""
    _g = globals()

    def ok(name: str) -> str:
        try:
            obj = _g.get(name)
            return "✅" if (obj is not None) else "❌"
        except Exception:
            return "❌"

    checks = [
        ("Help",    "HELP_TEXT"),
        ("Games",   "handle_game_command"),
        ("Economy", "handle_balance"),
        ("Shop",    "handle_shop"),
        ("Bank",    "handle_bank"),
        ("Casino",  "handle_bj"),
        ("RBJ",     "handle_rbj"),
        ("Events",  "handle_event"),
        ("Quests",  "handle_quests"),
        ("Achieve", "handle_achievements"),
        ("Rep",     "handle_rep"),
        ("Reports", "handle_report"),
        ("Staff",   "is_owner"),
        ("Maint",   "handle_botstatus"),
    ]

    parts = [f"{ok(sym)} {label}" for label, sym in checks]
    msg = " ".join(parts)
    await bot.highrise.send_whisper(user.id, msg[:245])


async def _cmd_launchcheck(bot, user):
    """Owner-only: quick launch readiness summary for public opening."""
    from modules.permissions import is_owner
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Owner only.")
        return

    _g = globals()

    def _chk(sym: str) -> str:
        return "OK" if _g.get(sym) is not None else "⚠️"

    cmd_ok   = "OK" if _g.get("handle_bj") and _g.get("handle_balance") else "⚠️"
    help_ok  = "OK" if _g.get("HELP_TEXT") else "⚠️"
    bots_ok  = "OK" if _g.get("handle_bothealth") else "⚠️"
    asst_ok  = "OK" if _g.get("handle_acesinatra") else "⚠️"
    notif_ok = "OK" if _g.get("handle_banknotify") or _g.get("handle_notify") else "⚠️"
    econ_ok  = "OK" if _g.get("handle_balance") and _g.get("handle_shop") else "⚠️"
    games_ok = "OK" if _g.get("handle_bj") and _g.get("handle_rbj") and _g.get("handle_poker") else "⚠️"

    try:
        from modules.cmd_audit import ROUTED_COMMANDS, HIDDEN_CMDS, DEPRECATED_CMDS
        from modules.multi_bot import _DEFAULT_COMMAND_OWNERS
        from modules.command_registry import get_entry as _reg_get
        active = ALL_KNOWN_COMMANDS - (HIDDEN_CMDS & ALL_KNOWN_COMMANDS) - (DEPRECATED_CMDS & ALL_KNOWN_COMMANDS)
        missing  = len(active - ROUTED_COMMANDS)
        noowner  = len(active - set(_DEFAULT_COMMAND_OWNERS.keys()))
        nohandle = len([c for c in active if _reg_get(c) is None])
        if missing == 0 and noowner == 0 and nohandle == 0:
            conf_txt = "none"
        else:
            parts = []
            if missing:  parts.append(f"{missing} no-route")
            if noowner:  parts.append(f"{noowner} no-owner")
            if nohandle: parts.append(f"{nohandle} no-handler")
            conf_txt = ", ".join(parts)
    except Exception:
        conf_txt = "skipped"

    all_ok = all(x == "OK" for x in [cmd_ok, help_ok, bots_ok, asst_ok, notif_ok, econ_ok, games_ok])
    if all_ok and conf_txt == "none":
        summary = "✅ All systems ready."
    else:
        summary = "⚠️ Use !commandissues." if conf_txt not in ("none", "skipped") else "⚠️ Check above."

    msg = (
        f"🚀 Launch Check\n"
        f"Commands: {cmd_ok}\nHelp: {help_ok}\nBots: {bots_ok}\n"
        f"Conflicts: {conf_txt}\n"
        f"Assistant: {asst_ok}\nNotifications: {notif_ok}\n"
        f"Economy: {econ_ok}\nGames: {games_ok}\n"
        f"{summary}"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])


# ---------------------------------------------------------------------------
# Bank notification delivery helper
# ---------------------------------------------------------------------------

# Track per-user delivery to avoid spamming on every chat message
_notif_delivered_this_session: set[str] = set()

# Autospawn cooldown tracker: user_id → last spawn time (epoch float)
_autospawn_cooldowns: dict[str, float] = {}
_AUTOSPAWN_COOLDOWN_S = 30


async def _autospawn_user_on_join(bot, user: User) -> None:
    """Teleport a joining user to their role spawn if autospawn is enabled."""
    import time as _time
    try:
        if db.get_room_setting("autospawn_enabled", "0") != "1":
            return
        now = _time.time()
        last = _autospawn_cooldowns.get(user.id, 0)
        if now - last < _AUTOSPAWN_COOLDOWN_S:
            return
        spawn = get_autospawn_spawn_for_user(user.username.lower())
        if not spawn:
            return
        _autospawn_cooldowns[user.id] = now
        from highrise.models import Position as _Pos
        pos = _Pos(
            x=float(spawn.get("x", 0)),
            y=float(spawn.get("y", 0)),
            z=float(spawn.get("z", 0)),
        )
        await bot.highrise.teleport(user.id, pos)
    except Exception as _exc:
        print(f"[autospawn] {user.username}: {_exc!r}")


async def _deliver_pending_bank_notifications(bot, user: User) -> None:
    """Deliver any queued bank notifications to *user* via whisper.

    Only marks delivered after a successful send.
    On failure, increments delivery_attempts and logs last_error.
    """
    username = user.username.lower().strip()
    pending = db.get_pending_bank_notifications(username)
    if not pending:
        return
    # Build the message
    try:
        if len(pending) == 1:
            r = pending[0]
            fee_note = f" Fee: {r['fee']} 🪙." if r["fee"] else ""
            msg = (
                f"🏦 You received {r['amount_received']:,} 🪙 from @{r['sender_username']}."
                f"{fee_note}"
            )[:249]
        elif len(pending) <= 3:
            lines = [f"🏦 {len(pending)} deposits while you were away:"]
            for r in pending:
                fee_note = f" Fee:{r['fee']} 🪙" if r["fee"] else ""
                lines.append(
                    f"+{r['amount_received']:,} 🪙 from @{r['sender_username']}{fee_note}"
                )
            msg = "\n".join(lines)[:249]
        else:
            total = sum(r["amount_received"] for r in pending)
            msg = (
                f"🏦 You have {len(pending)} deposits. "
                f"Total: {total:,} 🪙. Use /transactions."
            )[:249]
        await bot.highrise.send_whisper(user.id, msg)
        # Only mark delivered after the whisper succeeded
        db.mark_bank_notifications_delivered(username)
        _notif_delivered_this_session.add(username)
    except Exception as exc:
        err_str = str(exc)
        print(f"[BANK] Could not deliver pending notification to @{username}: {err_str}")
        for r in pending:
            db.record_notification_attempt_failed(r["id"], err_str)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 3.3A — AI delegated task system quarantined
# The old EmceeBot delegated task loop imported from modules.ai_assistant
# which is now quarantined as ai_assistant_old_broken.py.
# AceSinatra does not use cross-bot delegated tasks.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Bot startup logging + background task exception hook
# ---------------------------------------------------------------------------

print(f"[BOOT] mode={config.BOT_MODE} id={config.BOT_ID}"
      f" ts={bot_state.PROC_START.strftime('%H:%M:%S UTC')}")


def _install_task_exception_handler() -> None:
    """Route all unhandled background-task exceptions to the console."""
    import traceback as _tb

    def _handler(loop, ctx):
        exc  = ctx.get("exception")
        task = ctx.get("task")
        name = task.get_name() if task else ctx.get("message", "?")
        print(f"[TASK ERROR] {name} mode={config.BOT_MODE}")
        if exc:
            _tb.print_exception(type(exc), exc, exc.__traceback__)
            bot_state.LAST_ERROR = f"task:{name[:28]}"

    try:
        asyncio.get_event_loop().set_exception_handler(_handler)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Bot class
# ---------------------------------------------------------------------------

class HangoutBot(BaseBot):
    """
    Main bot class for the all-in-one HangoutBot.
    Inherits from Highrise's BaseBot and overrides event hooks.
    """

    async def on_start(self, session_metadata) -> None:
        """Called once when the bot successfully connects to the room."""
        print(f"[SDK] bot mode={BOT_MODE} ready")
        print(f"[HangoutBot] Connected — room {config.ROOM_ID} | DB: {config.DB_PATH}")
        print(f"[HangoutBot] SDK version: {_TIP_SDK_VERSION}")
        print(f"[HangoutBot] Run command: cd artifacts/highrise-bot && python3 bot.py")
        # Store bot identity so gold rain / tip receiver-check can use it
        set_bot_identity(session_metadata.user_id)
        print(f"[HangoutBot] Bot user ID: {session_metadata.user_id}")
        # Install SDK rate-limit guards — wraps send_whisper + chat on this fresh
        # Highrise() instance (SDK creates a new one each connect/reconnect)
        try:
            from modules.rate_limiter import install_rate_limiter
            install_rate_limiter(self.highrise, session_metadata.rate_limits)
        except Exception as _rle:
            print(f"[RATE] Guard install failed (non-fatal): {_rle}")

        # Resolve bot's own username immediately so the direct outfit listener
        # can match "KeanuShield, outfit status" right from the first message.
        # We fetch room users once inline; refresh_room_cache (below) will also
        # update the room cache in full — the extra call is negligible overhead.
        try:
            _ru_resp = await self.highrise.get_room_users()
            if hasattr(_ru_resp, "content"):
                for _ru, _ in _ru_resp.content:
                    if _ru.id == session_metadata.user_id:
                        set_bot_identity(session_metadata.user_id, _ru.username)
                        print(f"[HangoutBot] Bot username: {_ru.username}")
                        break
        except Exception as _e:
            print(f"[HangoutBot] Could not resolve bot username at startup: {_e}")

        # Log which events this session is subscribed to (only overridden hooks)
        try:
            from highrise.__main__ import gather_subscriptions
            subs = gather_subscriptions(self)
            print(f"[HangoutBot] Event subscriptions: {subs or '(all)'}")
        except Exception:
            pass
        # ── Bot lifecycle logging + task exception handler ────────────────────
        bot_state.RESTART_COUNT += 1
        _now_ts = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).strftime("%H:%M:%S UTC")
        if bot_state.RESTART_COUNT == 1:
            print(f"[START] mode={BOT_MODE} id={config.BOT_ID} first_connect @ {_now_ts}")
        else:
            bot_state.LAST_RECONNECT_AT = _now_ts
            print(f"[RECONNECT] mode={BOT_MODE} id={config.BOT_ID}"
                  f" reconnect #{bot_state.RESTART_COUNT - 1} @ {_now_ts}")
        _install_task_exception_handler()

        def _safe_task(coro, label: str):
            """Wrap a startup coroutine so one failure never kills the bot."""
            async def _guarded():
                try:
                    await coro
                except Exception as _e:
                    import traceback as _tb
                    print(f"[TASK ERROR] {label} failed: {_e!r}")
                    _tb.print_exc()
            return asyncio.create_task(_guarded())

        # Seed the room user cache from the live room list
        _safe_task(refresh_room_cache(self), "refresh_room_cache")
        # Recover active event — events bot only
        if should_this_bot_run_module("events"):
            _safe_task(startup_event_check(self),       "startup_event_check")
            _safe_task(startup_mining_event_check(self),"startup_mining_event_check")
        else:
            print(f"[EVENTS] Startup check skipped — not events bot ({BOT_MODE}).")
        # Recover BJ/RBJ tables — blackjack bot only
        if should_this_bot_run_module("blackjack"):
            _safe_task(startup_bj_recovery(self),  "startup_bj_recovery")
            _safe_task(startup_rbj_recovery(self), "startup_rbj_recovery")
        else:
            print(f"[BJ] Recovery skipped — not blackjack bot ({BOT_MODE}).")
        # Recover poker table — poker bot only
        if should_this_bot_run_module("poker"):
            _safe_task(startup_poker_recovery(self), "startup_poker_recovery")
        else:
            print(f"[POKER] Recovery skipped — not poker bot ({BOT_MODE}).")
        # Recover AutoMine / AutoFish sessions — miner/fisher bots only
        if should_this_bot_run_module("mining"):
            _safe_task(startup_automine_recovery(self), "startup_automine_recovery")
        else:
            print(f"[AUTOMINE] Recovery skipped — not miner bot ({BOT_MODE}).")
        if should_this_bot_run_module("fishing"):
            _safe_task(startup_autofish_recovery(self), "startup_autofish_recovery")
        else:
            print(f"[AUTOFISH] Recovery skipped — not fisher bot ({BOT_MODE}).")
        # Big announce reactor
        _safe_task(startup_big_announce_reactor(self), "startup_big_announce_reactor")
        # First-find announcer — host/all bots
        if BOT_MODE in ("host", "all"):
            _safe_task(startup_firstfind_announcer(self), "startup_firstfind_announcer")
        # First-find banker — banker/all bots
        if BOT_MODE in ("banker", "all"):
            _safe_task(startup_firstfind_banker(self), "startup_firstfind_banker")
            # Backfill donation leaderboard from tip_transactions (idempotent)
            try:
                n = db.backfill_gold_donations_from_tip_transactions()
                print(f"[BANKER] Donation backfill complete: {n} new row(s) added to gold_tip_events")
            except Exception as _bf_exc:
                print(f"[BANKER] Donation backfill error: {_bf_exc!r}")
        # Time-in-room EXP loop — host bot only
        if should_this_bot_run_module("timeexp"):
            _safe_task(time_exp_loop(self), "time_exp_loop")
        else:
            print(f"[TIME_EXP] Loop skipped — not host bot ({BOT_MODE}).")
        # Luxe Jail recovery + expiry loop — security bot only
        if should_this_bot_run_module("security"):
            _safe_task(startup_jail_recovery(self), "startup_jail_recovery")
        else:
            print(f"[JAIL] Recovery/expiry loop skipped — not security bot ({BOT_MODE}).")
        # Rotating announcements loop — host bot only
        if should_this_bot_run_module("host"):
            _safe_task(rotating_announcement_loop(self), "rotating_announcement_loop")
        else:
            print(f"[ANNOUNCE] Rotating loop skipped — not host bot ({BOT_MODE}).")
        # Background automation loops (idempotent — safe on reconnect)
        try:
            start_auto_game_loop(self)
        except Exception:
            import traceback; traceback.print_exc()
            print("[STARTUP ERROR] start_auto_game_loop failed — bot continues.")
        try:
            start_auto_event_loop(self)
        except Exception:
            import traceback; traceback.print_exc()
            print("[STARTUP ERROR] start_auto_event_loop failed — bot continues.")
        try:
            start_activity_prompt_loop(self)
        except Exception:
            import traceback; traceback.print_exc()
            print("[STARTUP ERROR] start_activity_prompt_loop failed — bot continues.")
        # Room interval message loop
        try:
            await start_interval_loop(self)
        except Exception:
            import traceback; traceback.print_exc()
            print("[STARTUP ERROR] start_interval_loop failed — bot continues.")
        # Multi-bot heartbeat
        _safe_task(start_multibot_heartbeat(self), "start_multibot_heartbeat")
        # 3.3A — AI delegated task loop removed (old EmceeBot system quarantined)
        # Startup safety checks (logs warnings only)
        try:
            check_startup_safety()
        except Exception:
            import traceback; traceback.print_exc()
            print("[STARTUP ERROR] check_startup_safety failed — bot continues.")
        # Startup room announce
        _safe_task(send_startup_announce(self), "send_startup_announce")
        # Bot spawn
        _safe_task(
            apply_bot_spawn(self, get_bot_username() or config.BOT_USERNAME),
            "apply_bot_spawn"
        )

    # ── on_chat safety wrapper ────────────────────────────────────────────────
    async def on_chat(self, user: User, message: str) -> None:
        """Crash-proof wrapper — no command can kill the bot."""
        try:
            await self._on_chat_impl(user, message)
        except Exception:
            import traceback as _tb
            print(f"[ON_CHAT ERROR] mode={BOT_MODE} user={user.username!r} msg={message!r}")
            _tb.print_exc()
            bot_state.LAST_ERROR = f"{user.username}:{message[:40]}"
            try:
                await self.highrise.send_whisper(
                    user.id, "⚠️ Command error logged. Bot stayed online."
                )
            except Exception:
                pass

    async def _on_chat_impl(self, user: User, message: str) -> None:
        """
        Called for every public chat message.
        Accepts ! commands. Redirects / commands to use ! instead.
        """
        message = message.strip()

        # ── / → ! redirect — tell players to use ! commands ──────────────────
        if message.startswith("/") and not message.startswith("//"):
            _slash_cmd = message.split()[0] if message.split() else "/"
            # Only redirect if it looks like a real command attempt (not a URL)
            if len(_slash_cmd) > 1 and not _slash_cmd.startswith("//"):
                _bang_equiv = "!" + _slash_cmd[1:]
                try:
                    await self.highrise.send_whisper(
                        user.id,
                        f"⚠️ Use ! commands only.\nTry: {_bang_equiv}"
                    )
                except Exception:
                    pass
                return

        # ── !help — FIRST THING, before AI intercept, before time-EXP,
        #    before everything.  Bulletproof: no DB, no registry, no name lookup.
        if message.lower().startswith("!help") or message.lower().startswith("/help"):
            try:
                await _handle_safe_help(self, user, message)
            except Exception:
                import traceback
                print(f"[HELP ERROR] !help failed  cmd=help  args={message!r}"
                      f"  user={user.username}  bot_mode={BOT_MODE}")
                traceback.print_exc()
                try:
                    await self.highrise.send_whisper(
                        user.id,
                        "Help is temporarily unavailable. Try !start or !balance."
                    )
                except Exception:
                    pass
            return

        # Track activity for time-EXP active bonus (any chat = active player)
        time_exp_record_activity(user.id)

        # ── Direct bot outfit listener — runs first for non-host bots ──────────
        # Handles "BotUsername, copy my outfit" etc. without AI delegation.
        # Host/eventhost bots skip this and use the full AI path below.
        if not (message.startswith("/") or message.startswith("!")):
            if await handle_direct_bot_outfit_chat(self, user, message):
                return

        # ── 3.3A — AceSinatra AI assistant (natural-language trigger) ────────────
        if await handle_acesinatra(self, user, message):
            return

        if not (message.startswith("/") or message.startswith("!")):
            # ── Jail / bail free-text confirm/cancel ─────────────────────────
            _msg_low = message.strip().lower()
            if _msg_low in ("confirm", "yes", "ok"):
                if await handle_jail_confirm(self, user):
                    return
            elif _msg_low in ("cancel", "no"):
                if await handle_jail_cancel(self, user):
                    return
            # Room assistant — greetings + Q&A (host bot only, with cooldowns)
            if await handle_room_assistant_chat(self, user, message):
                return
            # Direct auto-game answer detection (no /answer prefix needed)
            await try_direct_answer(self, user, message)
            return

        # Parse "/coinflip heads 50" → cmd="coinflip", args=["coinflip","heads","50"]
        parts = message[1:].split()
        if not parts:
            return

        cmd  = parts[0].lower()
        args = parts
        print(f"[RX] mode={BOT_MODE} user={user.username} text={message}")

        # ── Multi-bot gate — ignore if another bot owns this command ─────────
        if not should_this_bot_handle(cmd):
            offline_msg = get_offline_message(cmd)
            if offline_msg:
                await self.highrise.send_whisper(user.id, offline_msg)
            return

        # ── Deliver queued bank/subscriber notifications on first command ──
        _uname = user.username.lower().strip()
        if _uname not in _notif_delivered_this_session:
            asyncio.create_task(_deliver_pending_bank_notifications(self, user))
            asyncio.create_task(deliver_pending_subscriber_messages(self, _uname))

        # ── Early owner-only routes (bypass STAFF_CMDS block) ────────────────
        if cmd == "previewannounce":
            await handle_previewannounce(self, user, args)
            return

        # ── Jail teleport escape block ────────────────────────────────────────
        try:
            from modules.jail_enforcement import (
                is_jailed, jail_block_message, TELEPORT_BLOCKED_CMDS,
                can_send_jail_block_msg,
            )
            if cmd in TELEPORT_BLOCKED_CMDS and is_jailed(user.id):
                from modules.securitybot_jail import is_security_bot as _is_sec_blk
                if _is_sec_blk():
                    if can_send_jail_block_msg(user.id, cmd):
                        try:
                            await self.highrise.send_whisper(
                                user.id, jail_block_message(user.id)
                            )
                        except Exception as _bme:
                            print(f"[JAIL BLOCK MESSAGE ERROR] ignored: {_bme!r}")
                        print(
                            f"[JAIL BLOCK] user={user.username} cmd={cmd} "
                            f"announcer=KeanuShield message_sent=true"
                        )
                    else:
                        print(
                            f"[JAIL BLOCK] user={user.username} cmd={cmd} "
                            f"announcer=KeanuShield cooldown=true"
                        )
                else:
                    print(f"[JAIL BLOCK] user={user.username} cmd={cmd} blocked=true announcer=suppressed")
                return
        except Exception as _je:
            print(f"[JAIL BLOCK ERROR] ignored: {_je!r}")

        # ── Jail runtime commands (any user; KeanuShield/security gate via should_this_bot_handle)
        if cmd in {
            "jail", "bail", "jailstatus", "jailtime", "jailhelp",
            "jailconfirm", "jailcancel",
        }:
            print(f"[JAIL ROUTE HIT] runtime cmd={cmd} user={user.username} bot_mode={BOT_MODE}")
            if cmd == "jail":
                await handle_jail(self, user, args)
            elif cmd == "bail":
                await handle_bail(self, user, args)
            elif cmd in ("jailstatus", "jailtime"):
                await handle_jailstatus(self, user, args)
            elif cmd == "jailhelp":
                await handle_jailhelp(self, user, args)
            elif cmd == "jailconfirm":
                await handle_jail_confirm(self, user)
            elif cmd == "jailcancel":
                await handle_jail_cancel(self, user)
            return

        # ── Staff gate ────────────────────────────────────────────────────────
        if cmd in STAFF_CMDS:
            if cmd in OWNER_ONLY_CMDS:
                if not is_owner(user.username):
                    await self.highrise.send_whisper(user.id, "Owner only.")
                    return
            elif cmd in ADMIN_ONLY_CMDS:
                if not can_manage_economy(user.username):
                    await self.highrise.send_whisper(user.id, "Admins and owners only.")
                    return
            elif cmd in MANAGER_ONLY_CMDS:
                if not can_manage_games(user.username):
                    await self.highrise.send_whisper(user.id, "Managers and above only.")
                    return
            elif cmd in MOD_ONLY_CMDS:
                if not can_moderate(user.username):
                    await self.highrise.send_whisper(user.id, "Staff only.")
                    return

            if cmd == "setrbjlimits":
                await handle_setrbjlimits(self, user, args)
            elif cmd == "setbjlimits":
                await handle_setbjlimits(self, user, args)
            elif cmd.startswith("setrbj"):
                await handle_rbj_set(self, user, cmd, args)
            elif cmd.startswith("setbj"):
                await handle_bj_set(self, user, cmd, args)
            elif cmd in {"addowner", "removeowner",
                         "addmanager", "removemanager",
                         "addmoderator", "removemoderator",
                         "addadmin", "removeadmin"}:
                await _handle_staff_cmd(self, user, cmd, args)
            elif cmd == "admins":
                await _cmd_admins(self, user)
            elif cmd == "allstaff":
                await _cmd_allstaff(self, user, args)
            elif cmd == "allcommands":
                await _cmd_allcommands(self, user, args)
            elif cmd == "checkcommands":
                await _audit_checkcommands(self, user, args)
            elif cmd == "missingcommands":
                await handle_missingcommands(self, user)
            elif cmd == "routecheck":
                await handle_routecheck(self, user)
            elif cmd == "silentcheck":
                await handle_silentcheck(self, user, ALL_KNOWN_COMMANDS)
            elif cmd == "commandtest":
                await handle_commandtest(self, user, args)
            elif cmd == "fixcommands":
                await handle_fixcommands(self, user)
            elif cmd == "testcommands":
                await handle_testcommands(self, user)
            elif cmd == "commandintegrity":
                await handle_commandintegrity(self, user, ALL_KNOWN_COMMANDS)
            elif cmd == "commandrepair":
                await handle_commandrepair(self, user)
            elif cmd == "fixcommandregistry":
                await handle_commandrepair(self, user)
            elif cmd == "commandissues":
                await handle_commandissues(self, user, args, ALL_KNOWN_COMMANDS)
            elif cmd == "launchcheck":
                await _cmd_launchcheck(self, user)
            elif cmd == "currencycheck":
                await handle_currencycheck(self, user, args)
            elif cmd in {"commandtestall", "ctall"}:
                await handle_commandtestall(self, user, args, ALL_KNOWN_COMMANDS)
            elif cmd in {"commandtestgroup", "ctgroup"}:
                await handle_commandtestgroup(self, user, args, ALL_KNOWN_COMMANDS)
            elif cmd == "bankblock":
                await handle_bankblock(self, user, args, block=True)
            elif cmd == "bankunblock":
                await handle_bankblock(self, user, args, block=False)
            elif cmd == "banksettings":
                await handle_banksettings(self, user)
            elif cmd == "casinosettings":
                await handle_casinosettings(self, user, args)
            elif cmd == "casinolimits":
                await handle_casinolimits(self, user)
            elif cmd == "casinotoggles":
                await handle_casinotoggles(self, user, args)
            elif cmd == "ledger":
                await handle_ledger(self, user, args)
            elif cmd == "viewtx":
                await handle_viewtx(self, user, args)
            elif cmd == "bankwatch":
                await handle_bankwatch(self, user, args)
            elif cmd in BANK_ADMIN_SET_CMDS:
                await handle_bank_set(self, user, cmd, args)
            elif cmd == "audit":
                await handle_audit(self, user, args)
            elif cmd == "auditbank":
                await handle_auditbank(self, user, args)
            elif cmd == "auditcasino":
                await handle_auditcasino(self, user, args)
            elif cmd == "auditeconomy":
                await handle_auditeconomy(self, user, args)
            elif cmd == "auditlog":
                await handle_auditlog(self, user, args)
            elif cmd == "economyaudit":
                await handle_economyaudit(self, user, args)
            elif cmd == "gameprices":
                await handle_gameprices(self, user, args)
            elif cmd == "gameprice":
                await handle_gameprice(self, user, args)
            elif cmd == "setgameprice":
                await handle_setgameprice(self, user, args)
            elif cmd == "messageaudit":
                sub = args[1].lower() if len(args) > 1 else ""
                if sub == "help":
                    await handle_helpaudit(self, user, args, _build_audit_help_pages())
                else:
                    await handle_messageaudit(self, user, args)
            elif cmd == "helpaudit":
                await handle_helpaudit(self, user, args, _build_audit_help_pages())
            elif cmd == "setdailycoins":
                await handle_setdailycoins(self, user, args)
            elif cmd == "setgamereward":
                await handle_setgamereward(self, user, args)
            elif cmd == "setmaxbalance":
                await handle_setmaxbalance(self, user, args)
            elif cmd == "settransferfee":
                await handle_settransferfee(self, user, args)
            elif cmd == "mute":
                await handle_mute(self, user, args)
            elif cmd == "unmute":
                await handle_unmute(self, user, args)
            elif cmd == "mutes":
                await handle_mutes(self, user)
            elif cmd == "mutestatus":
                await handle_mutestatus(self, user, args)
            elif cmd == "forceunmute":
                await handle_forceunmute(self, user, args)
            elif cmd == "warn":
                await handle_warn(self, user, args)
            elif cmd == "warnings":
                await handle_warnings(self, user, args)
            elif cmd == "clearwarnings":
                await handle_clearwarnings(self, user, args)
            elif cmd in ("softban", "ecoban"):
                await handle_softban(self, user, args)
            elif cmd in ("unsoftban", "ecounban"):
                await handle_unsoftban(self, user, args)
            elif cmd == "staffnote":
                await handle_staffnote(self, user, args)
            elif cmd == "staffnotes":
                await handle_staffnotes(self, user, args)
            elif cmd == "permissioncheck":
                await handle_permissioncheck(self, user, args)
            elif cmd == "rolecheck":
                await handle_rolecheck(self, user, args)
            # ── Luxe Jail system (3.4A) ───────────────────────────────────
            elif cmd == "jail":
                await handle_jail(self, user, args)
            elif cmd == "bail":
                await handle_bail(self, user, args)
            elif cmd in ("jailstatus", "jailtime"):
                await handle_jailstatus(self, user, args)
            elif cmd == "jailhelp":
                await handle_jailhelp(self, user, args)
            elif cmd in ("unjail", "jailrelease"):
                await handle_unjail(self, user, args)
            elif cmd == "jailadmin":
                import os as _os; print(f"[JAIL ROUTE] cmd=jailadmin bot_mode={_os.getenv('BOT_MODE','?')} allowed=true calling=handle_jailadmin")
                await handle_jailadmin(self, user, args)
            elif cmd == "jailactive":
                await handle_jailactive(self, user, args)
            elif cmd == "jailsetcost":
                await handle_jailsetcost(self, user, args)
            elif cmd == "jailsetmax":
                await handle_jailsetmax(self, user, args)
            elif cmd == "jailsetmin":
                await handle_jailsetmin(self, user, args)
            elif cmd == "jailsetbailmultiplier":
                await handle_jailsetbailmultiplier(self, user, args)
            elif cmd == "jailprotectstaff":
                await handle_jailprotectstaff(self, user, args)
            elif cmd == "jaildebug":
                import os as _os; print(f"[JAIL ROUTE] cmd=jaildebug bot_mode={_os.getenv('BOT_MODE','?')} allowed=true calling=handle_jaildebug")
                await handle_jaildebug(self, user, args)
            elif cmd == "setjailspot":
                import os as _os; print(f"[JAIL ROUTE] cmd=setjailspot bot_mode={_os.getenv('BOT_MODE','?')} allowed=true calling=handle_setjailspot")
                await handle_setjailspot(self, user, args)
            elif cmd == "setjailguardspot":
                import os as _os; print(f"[JAIL ROUTE] cmd=setjailguardspot bot_mode={_os.getenv('BOT_MODE','?')} allowed=true calling=handle_setjailguardspot")
                await handle_setjailguardspot(self, user, args)
            elif cmd == "setsecurityidle":
                import os as _os; print(f"[JAIL ROUTE] cmd=setsecurityidle bot_mode={_os.getenv('BOT_MODE','?')} allowed=true calling=handle_setsecurityidle")
                await handle_setsecurityidle(self, user, args)
            elif cmd == "setjailreleasespot":
                import os as _os; print(f"[JAIL ROUTE] cmd=setjailreleasespot bot_mode={_os.getenv('BOT_MODE','?')} allowed=true calling=handle_setjailreleasespot")
                await handle_setjailreleasespot(self, user, args)
            elif cmd == "setrules":
                await handle_setrules(self, user, args)
            elif cmd == "automod":
                await handle_automod(self, user, args)
            elif cmd == "profileadmin":
                await handle_profileadmin(self, user, args)
            elif cmd == "profileprivacy":
                await handle_profileprivacy(self, user, args)
            elif cmd == "resetprofileprivacy":
                await handle_resetprofileprivacy(self, user, args)
            elif cmd == "replog":
                await handle_replog(self, user, args)
            elif cmd == "addrep":
                await handle_addrep(self, user, args)
            elif cmd == "removerep":
                await handle_removerep(self, user, args)
            elif cmd == "settiprate":
                await handle_settiprate(self, user, args)
            elif cmd == "settipcap":
                await handle_settipcap(self, user, args)
            elif cmd == "settiptier":
                await handle_settiptier(self, user, args)
            elif cmd == "settipautosub":
                await handle_settipautosub(self, user, args)
            elif cmd == "settipresubscribe":
                await handle_settipresubscribe(self, user, args)
            elif cmd == "setgametimer":
                await handle_setgametimer(self, user, args)
            elif cmd == "setautogameinterval":
                await handle_setautogameinterval(self, user, args)
            elif cmd == "setautoeventinterval":
                await handle_setautoeventinterval(self, user, args)
            elif cmd == "setautoeventduration":
                await handle_setautoeventduration(self, user, args)
            elif cmd == "gameconfig":
                await handle_gameconfig(self, user)
            elif cmd in ("goldtip", "tipgold", "goldreward", "rewardgold"):
                await handle_goldtip(self, user, args)
            elif cmd == "goldrefund":
                await handle_goldrefund(self, user, args)
            elif cmd in {"goldrain", "raingold", "goldstorm", "golddrop"}:
                await handle_goldrain(self, user, args)
            elif cmd == "goldrainstatus":
                await handle_goldrainstatus(self, user, args)
            elif cmd == "cancelgoldrain":
                await handle_cancelgoldrain(self, user, args)
            elif cmd == "goldrainhistory":
                await handle_goldrainhistory(self, user, args)
            elif cmd == "goldraininterval":
                await handle_goldraininterval(self, user, args)
            elif cmd == "setgoldraininterval":
                await handle_setgoldraininterval(self, user, args)
            elif cmd == "goldrainreplace":
                await handle_goldrainreplace(self, user, args)
            elif cmd == "goldrainpace":
                await handle_goldrainpace(self, user, args)
            elif cmd == "setgoldrainpace":
                await handle_setgoldrainpace(self, user, args)
            elif cmd == "msgcap":
                await handle_msgcap(self, user, args)
            elif cmd == "setmsgcap":
                await handle_setmsgcap(self, user, args)
            elif cmd == "goldrainall":
                await handle_goldrainall(self, user, args)
            elif cmd == "goldraineligible":
                await handle_goldraineligible(self, user, args)
            elif cmd == "goldrainrole":
                await handle_goldrainrole(self, user, args)
            elif cmd == "goldrainvip":
                await handle_goldrainvip(self, user, args)
            elif cmd == "goldraintitle":
                await handle_goldraintitle(self, user, args)
            elif cmd == "goldrainbadge":
                await handle_goldrainbadge(self, user, args)
            elif cmd == "goldrainlist":
                await handle_goldrainlist(self, user, args)
            elif cmd == "goldwallet":
                await handle_goldwallet(self, user, args)
            elif cmd == "goldtips":
                await handle_goldtips(self, user, args)
            elif cmd == "goldtx":
                await handle_goldtx(self, user, args)
            elif cmd == "pendinggold":
                await handle_pendinggold(self, user, args)
            elif cmd == "confirmgoldtip":
                await handle_confirmgoldtip(self, user, args)
            elif cmd == "setgoldrainstaff":
                await handle_setgoldrainstaff(self, user, args)
            elif cmd == "setgoldrainmax":
                await handle_setgoldrainmax(self, user, args)
            elif cmd == "goldtipbots":
                await handle_goldtipbots(self, user, args)
            elif cmd == "goldtipall":
                await handle_goldtipall(self, user, args)
            elif cmd == "debugtips":
                await handle_debugtips(self, user, args)
            elif cmd == "restarthelp":
                await handle_restarthelp(self, user)
            elif cmd == "restartstatus":
                await handle_restartstatus(self, user)
            elif cmd == "softrestart":
                await handle_softrestart(self, user)
            elif cmd == "restartbot":
                await handle_restartbot(self, user)
            elif cmd == "healthcheck":
                await handle_healthcheck(self, user)
            elif cmd == "announce_vip":
                await handle_announce_vip(self, user, args)
            elif cmd == "announce_staff":
                await handle_announce_staff(self, user, args)
            elif cmd == "debugsub":
                await handle_debugsub(self, user, args)
            elif cmd == "notifyuser":
                await handle_notifyuser(self, user, args)
            elif cmd == "broadcasttest":
                await handle_broadcasttest(self, user, args)
            elif cmd == "notifystats":
                await handle_notifystats(self, user, args)
            elif cmd == "notifyprefs":
                await handle_notifyprefs(self, user, args)
            elif cmd == "debugnotify":
                await handle_debugnotify(self, user, args)
            elif cmd == "testnotify":
                await handle_sub_testnotify(self, user, args)
            elif cmd == "testnotifyall":
                await handle_testnotifyall(self, user, args)
            elif cmd == "pendingnotify":
                await handle_pendingnotify(self, user, args)
            elif cmd == "clearpendingnotify":
                await handle_clearpendingnotify(self, user, args)
            elif cmd == "dailyadmin":
                await handle_dailyadmin(self, user, args)

            # ── Poker staff commands ──────────────────────────────────────────
            elif cmd == "resetbjlimits":
                target = args[1].lstrip("@") if len(args) > 1 else ""
                if not target:
                    await self.highrise.send_whisper(user.id, "Usage: !resetbjlimits <username>")
                else:
                    rec = db.find_or_stub_user(target)
                    db.reset_bj_daily_limits(rec["user_id"])
                    await self.highrise.send_whisper(user.id, f"✅ BJ daily limits reset for @{target}.")

            elif cmd == "resetrbjlimits":
                target = args[1].lstrip("@") if len(args) > 1 else ""
                if not target:
                    await self.highrise.send_whisper(user.id, "Usage: !resetrbjlimits <username>")
                else:
                    rec = db.find_or_stub_user(target)
                    db.reset_rbj_daily_limits(rec["user_id"])
                    await self.highrise.send_whisper(user.id, f"✅ RBJ daily limits reset for @{target}.")

            # ── Admin / owner power commands ──────────────────────────────────
            # Coins
            elif cmd in ("setcoins", "editcoins"):
                await handle_setcoins(self, user, args)
            elif cmd == "resetcoins":
                await handle_resetcoins(self, user, args)
            # Event coins
            elif cmd == "addeventcoins":
                await handle_addeventcoins(self, user, args)
            elif cmd == "removeeventcoins":
                await handle_removeeventcoins(self, user, args)
            elif cmd in ("seteventcoins", "editeventcoins"):
                await handle_seteventcoins(self, user, args)
            elif cmd == "reseteventcoins":
                await handle_reseteventcoins(self, user, args)
            # XP
            elif cmd == "addxp":
                await handle_addxp(self, user, args)
            elif cmd == "removexp":
                await handle_removexp(self, user, args)
            elif cmd in ("setxp", "editxp"):
                await handle_setxp(self, user, args)
            elif cmd == "resetxp":
                await handle_resetxp(self, user, args)
            # Level
            elif cmd in ("setlevel", "editlevel"):
                await handle_setlevel(self, user, args)
            elif cmd in ("addlevel", "promotelevel"):
                await handle_addlevel(self, user, args)
            elif cmd in ("removelevel", "demotelevel"):
                await handle_removelevel(self, user, args)
            # Rep (addrep/removerep already routed earlier in the elif chain)
            elif cmd in ("setrep", "editrep"):
                await handle_setrep(self, user, args)
            elif cmd == "resetrep":
                await handle_resetrep(self, user, args)
            # Titles
            elif cmd == "givetitle":
                await handle_givetitle(self, user, args)
            elif cmd == "removetitle":
                await handle_removetitle(self, user, args)
            elif cmd == "settitle":
                await handle_settitle(self, user, args)
            elif cmd == "cleartitle":
                await handle_cleartitle(self, user, args)
            # Badges (legacy shop + emoji system)
            elif cmd == "givebadge":
                await handle_givebadge_emoji(self, user, args)
            elif cmd == "removebadge":
                # Admin: !removebadge @user badge_id  — Player: !removebadge (unequip own)
                if is_admin(user.username) and len(args) > 2 and args[1].startswith("@"):
                    await handle_removebadge(self, user, args)
                else:
                    await handle_unequip_badge(self, user)
            elif cmd == "removebadgefrom":
                await handle_removebadge_emoji(self, user, args)
            elif cmd in {"setbadge", "equipbadge"}:
                # Admin: !setbadge @user badge_id  — Player: !setbadge badge_id
                if is_admin(user.username) and len(args) > 2 and args[1].startswith("@"):
                    await handle_setbadge(self, user, args)
                elif len(args) > 1:
                    await handle_equip_badge(self, user, args[1].lstrip("@").lower().strip())
                else:
                    await self.highrise.send_whisper(
                        user.id, "Usage: !equipbadge <badge_id>  See: !mybadges"
                    )
            elif cmd == "clearbadge":
                await handle_clearbadge(self, user, args)
            # Emoji Badge Market — admin commands
            elif cmd == "addbadge":
                await handle_addbadge(self, user, args)
            elif cmd == "editbadgeprice":
                await handle_editbadgeprice(self, user, args)
            elif cmd == "setbadgepurchasable":
                await handle_setbadgeflag(self, user, args, "purchasable")
            elif cmd == "setbadgetradeable":
                await handle_setbadgeflag(self, user, args, "tradeable")
            elif cmd == "setbadgesellable":
                await handle_setbadgeflag(self, user, args, "sellable")
            elif cmd == "giveemojibadge":
                await handle_giveemojibadge(self, user, args)
            elif cmd == "badgecatalog":
                await handle_badgecatalog(self, user, args)
            elif cmd == "badgeadmin":
                await handle_badgeadmin(self, user, args)
            elif cmd == "setbadgemarketfee":
                await handle_setbadgemarketfee(self, user, args)
            elif cmd == "badgemarketlogs":
                await handle_badgemarketlogs(self, user, args)
            # VIP
            elif cmd == "addvip":
                await handle_addvip(self, user, args)
            elif cmd == "removevip":
                await handle_removevip(self, user, args)
            elif cmd == "vips":
                await handle_vips(self, user, args)
            elif cmd == "setvipprice":
                await handle_setvipprice(self, user, args)
            # Casino resets
            elif cmd == "resetbjstats":
                await handle_resetbjstats(self, user, args)
            elif cmd == "resetrbjstats":
                await handle_resetrbjstats(self, user, args)
            elif cmd == "resetpokerstats":
                await handle_resetpokerstats(self, user, args)
            elif cmd == "resetcasinostats":
                await handle_resetcasinostats(self, user, args)
            # Admin tools
            elif cmd == "adminpanel":
                await handle_adminpanel(self, user, args)
            elif cmd == "adminlogs":
                await handle_adminlogs(self, user, args)
            elif cmd == "adminloginfo":
                await handle_adminloginfo(self, user, args)
            elif cmd == "checkhelp":
                await _audit_checkhelp(self, user, ALL_KNOWN_COMMANDS)
            elif cmd == "eventstart":
                await handle_startevent(self, user, args)
            elif cmd == "eventstop":
                await handle_stopevent(self, user, args)

            # ── Bot health / deployment checks (manager+) ─────────────────────
            elif cmd == "bothealth":
                await handle_bothealth(self, user, args)
            elif cmd == "modulehealth":
                try:
                    await handle_modulehealth(self, user, args)
                except Exception as _mh_err:
                    import traceback as _mh_tb
                    _mh_tb.print_exc()
                    print(f"[MODULEHEALTH ERROR] {_mh_err}")
                    try:
                        await self.highrise.send_whisper(
                            user.id,
                            "⚠️ modulehealth error logged. Bot stayed online.")
                    except Exception:
                        pass
            elif cmd == "deploymentcheck":
                await handle_deploymentcheck(self, user, args)
            elif cmd == "botlocks":
                await handle_botlocks(self, user)
            elif cmd == "botheartbeat":
                await handle_botheartbeat(self, user)
            elif cmd == "moduleowners":
                await handle_moduleowners(self, user, args)
            elif cmd == "botconflicts":
                try:
                    await handle_botconflicts(self, user)
                except Exception as _bc_err:
                    import traceback as _bc_tb
                    _bc_tb.print_exc()
                    print(f"[BOTCONFLICTS ERROR] {_bc_err}")
                    try:
                        await self.highrise.send_whisper(
                            user.id,
                            "⚠️ botconflicts error logged. Bot stayed online.")
                    except Exception:
                        pass
            # ── Bot health repair (admin+) ─────────────────────────────────────
            elif cmd == "dblockcheck":
                await handle_dblockcheck(self, user, args)
            elif cmd == "clearstalebotlocks":
                await handle_clearstalebotlocks(self, user)
            elif cmd == "fixbotowners":
                await handle_fixbotowners(self, user, args)

            # ── 3.1Q staff commands ────────────────────────────────────────────
            elif cmd == "announce":
                await handle_announce_room(self, user, args)

            elif cmd == "staffdash":
                await handle_staffdash(self, user)
            elif cmd == "stafftools":
                await handle_stafftools(self, user)

            # ── Luxe Ticket admin (moved here — in STAFF_CMDS, outer elif is dead) ─
            elif cmd == "addtickets":
                await handle_addtickets(self, user, args)
            elif cmd == "removetickets":
                await handle_removetickets(self, user, args)
            elif cmd == "settickets":
                await handle_settickets(self, user, args)
            elif cmd == "sendtickets":
                await handle_sendtickets(self, user, args)
            elif cmd == "ticketbalance":
                await handle_ticketbalance(self, user, args)
            elif cmd == "ticketlogs":
                await handle_ticketlogs(self, user, args)
            elif cmd == "ticketadmin":
                await handle_ticketadmin(self, user)

            # ── Tip / conversion audit ────────────────────────────────────────────
            elif cmd == "tipaudit":
                print(f"[TIP AUDIT CMD] bot={BOT_MODE} cmd=tipaudit "
                      f"user={user.username} args={args}")
                await handle_tipaudit(self, user, args)
            elif cmd == "tipauditdetails":
                print(f"[TIP AUDIT CMD] bot={BOT_MODE} cmd=tipauditdetails "
                      f"user={user.username} args={args}")
                await handle_tipauditdetails(self, user, args)
            elif cmd == "conversionlogs":
                print(f"[TIP AUDIT CMD] bot={BOT_MODE} cmd=conversionlogs "
                      f"user={user.username} args={args}")
                await handle_conversionlogs(self, user, args)

            else:
                await handle_admin_command(self, user, cmd, args)
            return

        # ── Maintenance gate — block gameplay/economy during maintenance ──────
        _MAINT_BLOCKED = (
            GAME_COMMANDS | BJ_COMMANDS
            | {"poker", "daily", "send", "buy",
               "claimachievements", "claimquest", "buyevent"}
        )
        if is_maintenance() and cmd in _MAINT_BLOCKED:
            if not can_moderate(user.username):
                await self.highrise.send_whisper(
                    user.id,
                    "🔧 Maintenance mode is ON. This feature is temporarily unavailable."
                )
                return

        # ── Mute gate — block muted players from economy/game commands ────────
        _MUTE_EXEMPT = {
            "help", "casinohelp", "gamehelp", "coinhelp", "profilehelp",
            "shophelp", "progresshelp", "bankhelp", "staffhelp", "modhelp",
            "managerhelp", "adminhelp", "ownerhelp", "questhelp",
            "bjhelp", "rbjhelp", "pokerhelp", "viphelp", "tiphelp",
            "rephelp", "eventhelp", "reporthelp", "roleshelp",
            "mycommands", "helpsearch",
            "profile", "me", "whois", "pinfo",
            "stats", "badges", "titles", "privacy",
            "wallet", "w", "dash", "dashboard", "casinodash", "mycasino",
            "level", "balance", "bal", "b", "coins", "coin", "money", "myitems", "inventory", "inv",
            "myreports", "report", "bug",
            "botstatus", "maintenance",
            "rules", "warnings", "vipstatus",
            # Room utility exempt
            "roomhelp", "teleporthelp", "emotehelp", "alerthelp", "welcomehelp",
            "socialhelp", "botmodehelp", "spawns",
        }
        if cmd not in _MUTE_EXEMPT:
            _mute = db.get_active_mute(user.id)
            if _mute:
                await self.highrise.send_whisper(
                    user.id,
                    "🔇 You are bot-muted. Try again later."
                )
                return

        # ── Room-ban gate — block room-banned users ─────────────────────────
        _BAN_EXEMPT = _MUTE_EXEMPT | {"unban"}
        if cmd not in _BAN_EXEMPT and not can_moderate(user.username):
            try:
                if db.is_room_banned(user.username):
                    await self.highrise.send_whisper(
                        user.id, "⛔ You are banned from using bot commands."
                    )
                    return
            except Exception:
                pass

        # ── AutoMod check (spam / abuse detection) ─────────────────────────
        if cmd not in _MUTE_EXEMPT:
            _blocked = await automod_check(self, user, cmd, message)
            if _blocked:
                return

        # ── Public: /rules ────────────────────────────────────────────────────
        if cmd == "rules":
            await handle_rules(self, user)
            return

        # ── Economy commands ──────────────────────────────────────────────────
        if cmd in {"balance", "bal", "b", "coins", "coin", "money"}:
            print(f"[BALANCE] mode={BOT_MODE} user={user.username} cmd={cmd}")
            await handle_balance(self, user, args)
            return

        elif cmd in {"wallet", "w"}:
            print(f"[WALLET] mode={BOT_MODE} user={user.username} cmd={cmd}")
            await handle_wallet(self, user, args)
            return

        elif cmd in {"casinodash", "mycasino"}:
            await handle_casino_dash(self, user, args)

        elif cmd == "dash":
            await handle_dashboard(self, user, args)

        elif cmd == "dashboard":
            await handle_ownerpanel(self, user)

        elif cmd in {"botdashboard", "botsystem"}:
            await handle_sys_dashboard(self, user, args)

        elif cmd == "daily":
            await handle_daily(self, user)

        elif cmd == "streak":
            await handle_streak(self, user)

        elif cmd == "dailystatus":
            await handle_dailystatus(self, user)

        elif cmd == "commandcheck":
            await self.highrise.send_whisper(user.id,
                "Use !commandtest [command].\n"
                "Example: !commandtest !daily"
            )

        elif cmd == "dailies":
            await handle_dailyquests(self, user)

        elif cmd == "claimdaily":
            await handle_claimquest(self, user, args)

        elif cmd in {"leaderboard", "lb", "top"}:
            await handle_leaderboard(self, user)

        elif cmd == "toprich":
            await handle_toprich(self, user)

        elif cmd == "topminers":
            await handle_topminers(self, user)

        elif cmd == "topfishers":
            await handle_topfishers(self, user)

        elif cmd == "topstreaks":
            await handle_topstreaks(self, user)

        elif cmd == "toptippers":
            await handle_toptippers(self, user)

        elif cmd in {"toptipped", "toptipreceivers"}:
            await handle_toptipped(self, user)

        elif cmd == "p2pgolddebug":
            await handle_p2pgolddebug(self, user, args)

        elif cmd in {"profile", "me", "whois", "pinfo", "myprofile"}:
            await handle_profile_cmd(self, user, args)
            asyncio.create_task(check_tutorial_step(self, user, "profile"))

        elif cmd == "stats":
            await handle_stats_cmd(self, user, args)

        elif cmd in {"flex", "showoff", "card"}:
            await handle_flex(self, user, args)

        elif cmd == "profilesettings":
            await handle_profile_settings(self, user, ["profile", "settings"] + args[1:])

        elif cmd == "profilehelp":
            await handle_profile_help(self, user)

        elif cmd in {"badges", "badgeshop"}:
            await handle_badges_cmd_router(self, user, args)

        elif cmd == "titles":
            await handle_badges_cmd(self, user, args)

        elif cmd == "privacy":
            await handle_privacy(self, user, args)

        elif cmd == "level":
            await handle_level(self, user)

        elif cmd == "xpleaderboard":
            await handle_xp_leaderboard(self, user)

        # ── Shop commands ─────────────────────────────────────────────────────
        elif cmd == "shop":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "badges":
                await handle_badges_cmd_router(self, user, args[1:])
            elif sub in ("next", "prev", "page"):
                # If the active session is badges, redirect to category browsing
                session = db.get_shop_session(user.username) if sub in ("next", "prev") else None
                if session and session.get("shop_type") == "badges":
                    await self.highrise.send_whisper(
                        user.id,
                        "Use direct pages:\n"
                        "!badges animals 2\n"
                        "!badges rare 1\n"
                        "!badges search crown"
                    )
                else:
                    await handle_shop_nav(self, user, args)
            else:
                await handle_shop(self, user, args)
            asyncio.create_task(check_tutorial_step(self, user, "shop"))

        elif cmd in ("buy", "buyitem", "purchase"):
            sub = args[1].lower() if len(args) > 1 else ""
            # /buy badge <id>  — direct ID buy (emoji badges)
            if sub == "badge" and len(args) > 2:
                await handle_buy_badge(self, user, args[2])
            # /buy title <id>  — direct ID buy (old shop)
            elif sub == "title" and len(args) > 2:
                await handle_buy(self, user, args)
            # /buy <number>  — numbered session buy
            elif sub.isdigit():
                await handle_buy_number(self, user, args)
            # /buy with no valid sub
            elif sub in ("badge", "title"):
                await self.highrise.send_whisper(user.id, f"Usage: !buy {sub} <id>")
            else:
                await self.highrise.send_whisper(
                    user.id,
                    "Buy by number: !buy <#> (open !shop badges first)\n"
                    "Or by ID: !buy badge <id>  !buy title <id>"
                )

        elif cmd == "equip":
            # Route /equip badge to emoji badge handler
            if len(args) > 1 and args[1].lower() == "badge":
                badge_id = args[2] if len(args) > 2 else ""
                if badge_id:
                    await handle_equip_badge(self, user, badge_id)
                else:
                    await self.highrise.send_whisper(user.id, "Usage: !equip badge <id>")
            else:
                await handle_equip(self, user, args)

        elif cmd == "unequip":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "badge":
                await handle_unequip_badge(self, user)
            else:
                await self.highrise.send_whisper(user.id, "Usage: !unequip badge")

        elif cmd in {"myitems", "inventory", "inv"}:
            await handle_myitems(self, user)

        elif cmd == "mybadges":
            await handle_mybadges(self, user)

        elif cmd == "badgeinfo":
            await handle_badgeinfo_emoji(self, user, args)

        elif cmd == "titleinfo":
            await handle_titleinfo(self, user, args)

        # ── Numbered shop extras ───────────────────────────────────────────────
        elif cmd in ("confirm", "jailconfirm"):
            await handle_jail_confirm(self, user)
        elif cmd in ("cancel", "jailcancel"):
            await handle_jail_cancel(self, user)
        elif cmd == "confirmbuy":
            await handle_confirmbuy(self, user, args)

        elif cmd == "cancelbuy":
            await handle_cancelbuy(self, user, args)

        elif cmd == "marketbuy":
            # /marketbuy <number> uses market_badges session
            if len(args) > 1 and args[1].isdigit():
                session = db.get_shop_session(user.username)
                if session and session["shop_type"] == "market_badges":
                    n    = int(args[1])
                    item = next((i for i in session["items"] if i["num"] == n), None)
                    if item:
                        await handle_badgebuy(self, user, ["badgebuy", str(item["listing_id"])])
                    else:
                        await self.highrise.send_whisper(user.id, "Invalid number. Open !badgemarket first.")
                else:
                    await self.highrise.send_whisper(user.id, "Open !badgemarket first.")
            else:
                await self.highrise.send_whisper(user.id, "Usage: !marketbuy <#>  (open !badgemarket first)")

        elif cmd == "shopadmin":
            await handle_shopadmin(self, user, args)

        elif cmd == "shoptest":
            await handle_shoptest(self, user, args)

        elif cmd == "setshopconfirm":
            await handle_setshopconfirm(self, user, args)

        elif cmd == "seteventconfirm":
            await handle_seteventconfirm(self, user, args)

        # ── Badge marketplace (player commands) ────────────────────────────────
        elif cmd == "badgemarket":
            await handle_badgemarket_nav(self, user, args)

        elif cmd == "badgelist":
            await handle_badgelist(self, user, args)

        elif cmd == "badgebuy":
            # /badgebuy <number> — number refers to position on last viewed market page
            # /badgebuy <listing_id> — direct listing ID (also works)
            raw = args[1] if len(args) > 1 else ""
            if raw.isdigit():
                session = db.get_shop_session(user.username)
                if session and session["shop_type"] == "market_badges":
                    n    = int(raw)
                    item = next((i for i in session["items"] if i["num"] == n), None)
                    if item:
                        await handle_badgebuy(self, user, ["badgebuy", str(item["listing_id"])])
                    else:
                        await handle_badgebuy(self, user, args)  # fall back: treat as listing_id
                else:
                    await handle_badgebuy(self, user, args)  # no session: treat as listing_id
            else:
                await handle_badgebuy(self, user, args)

        elif cmd == "badgecancel":
            await handle_badgecancel(self, user, args)

        elif cmd == "mybadgelistings":
            await handle_mybadgelistings(self, user)

        elif cmd == "badgeprices":
            await handle_badgeprices(self, user, args)

        elif cmd == "buybadge":
            await handle_buybadge_cmd(self, user, args)

        elif cmd in {"marketbadges", "badgemarkets"}:
            await handle_badgemarket_nav(self, user, args)

        elif cmd == "sellbadge":
            await handle_badgelist(self, user, args)

        elif cmd == "cancelbadge":
            await handle_badgecancel(self, user, args)

        elif cmd in {"mylistings", "mybadgelistings"}:
            await handle_mybadgelistings(self, user, args)

        elif cmd == "marketsearch":
            await handle_marketsearch(self, user, args)

        elif cmd == "marketfilter":
            await handle_marketfilter(self, user, args)

        elif cmd == "marketaudit":
            await handle_marketaudit(self, user)

        elif cmd == "marketdebug":
            await handle_marketdebug(self, user, args)

        elif cmd == "forcelistingcancel":
            await handle_forcelistingcancel(self, user, args)

        elif cmd == "clearbadgelocks":
            await handle_clearbadgelocks(self, user)

        elif cmd in {"helpmarket", "markethelp"}:
            await handle_market_help(self, user)

        elif cmd in {"helptrade", "tradehelp"}:
            await handle_trade_help(self, user)

        elif cmd == "trade":
            await handle_trade_start(self, user, args)

        elif cmd == "tradeadd":
            await handle_tradeadd(self, user, args)

        elif cmd == "tradecoins":
            await handle_tradecoins(self, user, args)

        elif cmd == "tradeview":
            await handle_tradeview(self, user)

        elif cmd == "tradeconfirm":
            await handle_tradeconfirm(self, user)

        elif cmd == "tradecancel":
            await handle_tradecancel(self, user)

        elif cmd == "staffbadge":
            await handle_staffbadge(self, user, args)

        elif cmd == "emojitest":
            await handle_emojitest(self, user, args)

        elif cmd == "disableemoji":
            await handle_disableemoji(self, user, args)

        elif cmd == "enableemoji":
            await handle_enableemoji(self, user, args)

        # ── Event commands ────────────────────────────────────────────────────
        elif cmd == "event":
            await handle_event(self, user, args)

        elif cmd == "eventschedule":
            await handle_event(self, user, ["event", "schedule"])

        elif cmd == "eventactive":
            await handle_event(self, user, ["event", "active"])

        elif cmd == "eventnext":
            await handle_nextevent(self, user)

        elif cmd in {"seasonpayout", "payouthistory"}:
            await handle_retentionadmin(self, user, ["retentionadmin", cmd, *args[1:]])

        elif cmd == "events":
            await handle_events(self, user)
            asyncio.create_task(check_tutorial_step(self, user, "events"))

        elif cmd in ("nextevent", "next"):
            await handle_nextevent(self, user)

        elif cmd == "schedule":
            await handle_events(self, user)

        elif cmd == "eventloop":
            await handle_eventloop(self, user, args)

        elif cmd == "eventstatus":
            await handle_eventstatus(self, user)

        elif cmd == "eventadmin":
            await handle_eventadmin(self, user, args)

        elif cmd == "startevent":
            await handle_startevent(self, user, args)

        elif cmd == "stopevent":
            await handle_stopevent(self, user, args)

        elif cmd in ("stopae", "stopautoevent", "endevent", "endcurrentevent"):
            await handle_stopevent(self, user, args)

        elif cmd in ("aeskip", "skipae", "skipaevent"):
            await handle_aeskip(self, user, args)

        elif cmd in ("aeskipnext", "skipnextae"):
            await handle_aeskipnext(self, user, args)

        elif cmd in ("adminsblessing", "adminblessing"):
            await handle_adminsblessing(self, user, args)

        elif cmd == "eventresume":
            await handle_eventresume(self, user)

        elif cmd == "autogamestatus":
            await handle_autogamestatus(self, user)

        elif cmd == "autogameresume":
            await handle_autogameresume(self, user)

        elif cmd == "mineevents":
            await handle_mineevents(self, user)

        elif cmd == "mineboosts":
            await handle_mineboosts(self, user)

        elif cmd == "luckstatus":
            await handle_luckstatus(self, user)

        elif cmd == "miningblessing":
            await handle_miningblessing(self, user, args)

        elif cmd == "luckevent":
            await handle_luckevent(self, user, args)

        elif cmd == "miningeventstart":
            await handle_miningevent_start(self, user, args)

        elif cmd == "eventmanager":
            await handle_eventmanager(self, user)

        elif cmd == "eventpreset":
            await handle_eventpreset(self, user, args)

        elif cmd == "eventpanel":
            await handle_eventpanel(self, user)

        elif cmd == "eventeffects":
            await handle_eventeffects(self, user)

        elif cmd == "autoeventstatus":
            await handle_autoeventstatus(self, user)

        elif cmd == "autoeventadd":
            await handle_autoeventadd(self, user, args)

        elif cmd == "autoeventremove":
            await handle_autoeventremove(self, user, args)

        elif cmd == "autoeventinterval":
            await handle_autoeventinterval(self, user, args)

        # ── Event Manager catalog + pool (new) ──────────────────────────────
        elif cmd == "eventlist":
            await handle_eventlist(self, user, args)

        elif cmd == "eventpreview":
            await handle_eventpreview(self, user, args)

        elif cmd in ("aepool", "autoeventpool"):
            await handle_aepool(self, user)

        elif cmd == "aeadd":
            await handle_aeadd(self, user, args)

        elif cmd == "aeremove":
            await handle_aeremove(self, user, args)

        elif cmd in ("aequeue", "autoeventqueue"):
            await handle_aequeue(self, user)

        elif cmd in ("aenext", "autoeventnext"):
            await handle_aenext(self, user)

        elif cmd in ("aestatus",):
            await handle_autoeventstatus(self, user)

        elif cmd in ("eventheartbeat", "eventscheduler"):
            await handle_eventheartbeat(self, user)

        elif cmd == "eventcooldowns":
            await handle_eventcooldowns(self, user, args)

        elif cmd == "seteventcooldown":
            await handle_seteventcooldown(self, user, args)

        elif cmd == "eventweights":
            await handle_eventweights(self, user, args)

        elif cmd == "seteventweight":
            await handle_seteventweight(self, user, args)

        elif cmd == "eventhistory":
            await handle_eventhistory(self, user)

        elif cmd in ("aehistory", "autoeventhistory"):
            await handle_aehistory(self, user)

        elif cmd in ("setaeinterval", "setautoeventinterval"):
            await handle_setaeinterval(self, user, args)

        elif cmd in ("setaeduration", "setautoeventduration", "seteventduration"):
            await handle_setaeduration(self, user, args)

        elif cmd == "aeinterval":
            await handle_aeinterval(self, user)

        elif cmd == "aeduration":
            await handle_aeduration(self, user)

        elif cmd in ("aererollnext", "rerollae"):
            await handle_aererollnext(self, user)

        elif cmd in ("setnextae", "setnextautoevent"):
            await handle_setnextae(self, user, args)

        elif cmd == "eventpoints":
            await handle_eventpoints(self, user, args)

        elif cmd == "eventshop":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub in ("next", "prev"):
                await handle_eventshop_nav(self, user, args)
            elif sub == "buy" and len(args) > 2:
                # /eventshop buy <item_id> — legacy alias
                await handle_buyevent(self, user, ["buyevent", args[2]])
            else:
                await handle_eventshop(self, user)

        elif cmd == "buyevent":
            await handle_buyevent(self, user, args)

        # ── Auto game / event toggle commands (public status, manager+ on/off) ─
        elif cmd == "autogames":
            await handle_autogames(self, user, args)

        elif cmd == "autoevents":
            await handle_autoevents(self, user, args)

        elif cmd == "autogamesowner":
            await handle_autogamesowner(self, user, args)

        elif cmd in ("stopautogames", "killautogames"):
            await handle_stopautogames(self, user)
        elif cmd == "fixautogames":
            await handle_fixautogames(self, user)

        # ── Achievement commands ───────────────────────────────────────────────
        elif cmd == "achievements":
            await handle_achievements(self, user, args)

        elif cmd == "claimachievements":
            await handle_claim_achievements(self, user)

        # ── Blackjack ─────────────────────────────────────────────────────────
        elif cmd == "bj":
            await handle_rbj(self, user, args)

        elif cmd == "rbj":
            await handle_rbj(self, user, args)

        # BJ short aliases — now all routed to the shoe engine
        elif cmd == "bjoin":
            bet_arg = args[1] if len(args) > 1 else ""
            if bet_arg:
                await handle_bet(self, user, ["bet", bet_arg])
            else:
                await handle_rbj(self, user, ["bj", "help"])

        elif cmd == "bt":
            await handle_rbj(self, user, ["bj", "table"])

        elif cmd == "bh":
            await handle_hit(self, user)

        elif cmd == "bs":
            await handle_stand(self, user)

        elif cmd == "bd":
            await handle_double(self, user)

        elif cmd == "bsp":
            await handle_split(self, user)

        elif cmd == "bi":
            await handle_insurance(self, user)

        elif cmd == "blimits":
            await handle_rbj(self, user, ["bj", "limits"])

        elif cmd == "bstats":
            await handle_rbj(self, user, ["bj", "stats"])

        elif cmd == "bhand":
            await handle_rbj(self, user, ["bj", "hand"])

        # RBJ short aliases
        elif cmd == "rjoin":
            bet_arg = args[1] if len(args) > 1 else ""
            await handle_rbj(self, user, ["rbj", "join", bet_arg] if bet_arg else ["rbj", "join"])

        elif cmd == "rt":
            await handle_rbj(self, user, ["rbj", "table"])

        elif cmd == "rh":
            await handle_rbj(self, user, ["rbj", "hit"])

        elif cmd == "rs":
            await handle_rbj(self, user, ["rbj", "stand"])

        elif cmd == "rd":
            await handle_rbj(self, user, ["rbj", "double"])

        elif cmd == "rsp":
            await handle_rbj(self, user, ["rbj", "split"])

        elif cmd == "rshoe":
            await handle_rbj(self, user, ["rbj", "shoe"])

        elif cmd == "rlimits":
            await handle_rbj(self, user, ["rbj", "limits"])

        elif cmd == "rstats":
            await handle_rbj(self, user, ["rbj", "stats"])

        elif cmd == "rhand":
            await handle_rbj(self, user, ["rbj", "hand"])

        # BJ full-prefix aliases (bjh, bjs, bjd, bjsp, bjhand)
        elif cmd == "bjh":
            await handle_hit(self, user)

        elif cmd == "bjs":
            await handle_stand(self, user)

        elif cmd == "bjd":
            await handle_double(self, user)

        elif cmd == "bjsp":
            await handle_split(self, user)

        elif cmd == "bjhand":
            await handle_rbj(self, user, ["bj", "hand"])

        # RBJ full-prefix aliases (rbjh, rbjs, rbjd, rbjsp, rbjhand)
        elif cmd == "rbjh":
            await handle_hit(self, user)

        elif cmd == "rbjs":
            await handle_stand(self, user)

        elif cmd == "rbjd":
            await handle_double(self, user)

        elif cmd == "rbjsp":
            await handle_split(self, user)

        elif cmd == "rbjhand":
            await handle_rbj(self, user, ["rbj", "hand"])

        # ── Easy BJ / universal shortcuts (all map to shoe engine) ───────────
        elif cmd == "blackjack":
            bet_arg = args[1] if len(args) > 1 else ""
            if bet_arg:
                await handle_bet(self, user, ["bet", bet_arg])
            else:
                await handle_rbj(self, user, ["bj", "help"])

        elif cmd == "bjbet":
            bet_arg = args[1] if len(args) > 1 else ""
            if bet_arg:
                await handle_bet(self, user, ["bet", bet_arg])
            else:
                await handle_rbj(self, user, ["bj", "help"])

        elif cmd == "bet":
            _poker_seated = False
            try:
                from modules.poker import _get_seated
                _poker_seated = _get_seated(user.username) is not None
            except Exception:
                pass
            if _poker_seated:
                bet_arg = args[1] if len(args) > 1 else ""
                await self.highrise.send_whisper(
                    user.id,
                    f"You're at the poker table. Use /pbet {bet_arg} for poker."
                )
            else:
                await handle_bet(self, user, args)

        elif cmd == "hit":
            await handle_hit(self, user)

        elif cmd == "stay":
            await handle_stand(self, user)

        elif cmd == "stand":
            await handle_stand(self, user)

        elif cmd == "double":
            await handle_double(self, user)

        elif cmd == "split":
            await handle_split(self, user)

        elif cmd == "insurance":
            await handle_insurance(self, user)

        elif cmd in {"surrender", "bsurrender"}:
            await handle_surrender(self, user)

        elif cmd == "bjforce":
            await handle_bjforce(self, user, args)

        elif cmd == "bjtest":
            await handle_bjtest(self, user, args)

        elif cmd in {"bjadmin", "bjadminhelp", "staffbj", "staffbjhelp"}:
            await handle_bjadmin(self, user, args)

        elif cmd == "bjshoe":
            await handle_rbj(self, user, ["rbj", "shoe"])
        elif cmd == "bjshoereset":
            if not (is_owner(user.username) or is_admin(user.username)):
                await self.highrise.send_whisper(user.id, "Admin/owner only.")
            else:
                try:
                    await handle_bj_shoe_reset(self, user)
                except Exception as _exc:
                    await self.highrise.send_whisper(user.id,
                        f"❌ Shoe reset failed: {str(_exc)[:80]}")

        elif cmd == "shoe":
            await handle_rbj(self, user, ["rbj", "shoe"])

        # ── How-to-play / game guide ──────────────────────────────────────────
        elif cmd in ("howtoplay", "gameguide", "games"):
            await _handle_howtoplay(self, user, args)

        # ── Time-in-Room EXP admin commands ───────────────────────────────────
        elif cmd == "settimeexp":
            await handle_settimeexp(self, user, args)
        elif cmd == "settimeexpcap":
            await handle_settimeexpcap(self, user, args)
        elif cmd == "settimeexptick":
            await handle_settimeexptick(self, user, args)
        elif cmd == "settimeexpbonus":
            await handle_settimeexpbonus(self, user, args)
        elif cmd == "timeexpstatus":
            await handle_timeexpstatus(self, user, args)
        elif cmd == "setallowbotxp":
            await handle_setallowbotxp(self, user, args)

        # ── Display format settings ────────────────────────────────────────────
        elif cmd == "displaybadges":
            await handle_displaybadges(self, user, args)
        elif cmd == "displaytitles":
            await handle_displaytitles(self, user, args)
        elif cmd == "displayformat":
            await handle_displayformat(self, user, args)
        elif cmd == "displaytest":
            await handle_displaytest(self, user, args)

        elif cmd == "botmessageformat":
            await handle_botmessageformat(self, user, args)
        elif cmd == "setbotmessageformat":
            await handle_setbotmessageformat(self, user, args)

        elif cmd == "msgtest":
            await handle_msgtest(self, user, args)
        elif cmd == "msgboxtest":
            await handle_msgboxtest(self, user, args)
        elif cmd == "msgsplitpreview":
            await handle_msgsplitpreview(self, user, args)

        elif cmd == "confirmcasinoreset":
            if not can_moderate(user.username):
                await self.highrise.send_whisper(user.id, "Staff only.")
            else:
                pending = _pending_casino_reset.get(user.id)
                if not pending:
                    await self.highrise.send_whisper(
                        user.id, "No pending casino reset. Use /casino reset first."
                    )
                elif asyncio.get_event_loop().time() - pending["ts"] > 60:
                    _pending_casino_reset.pop(user.id, None)
                    await self.highrise.send_whisper(
                        user.id, "Confirmation expired. Use !casino reset again."
                    )
                elif len(args) < 2 or args[1] != pending["code"]:
                    await self.highrise.send_whisper(
                        user.id, f"Wrong code. Expected: !confirmcasinoreset {pending['code']}"
                    )
                else:
                    _pending_casino_reset.pop(user.id, None)
                    bj_reset_table()
                    rbj_reset_table()
                    poker_reset_table()
                    await self.highrise.chat("✅ Casino tables reset (BJ, RBJ, Poker). Bets refunded.")

        # ── Bank player commands ───────────────────────────────────────────────
        elif cmd == "bank":
            await handle_bank(self, user, args)

        elif cmd == "send":
            await handle_send(self, user, args)

        elif cmd == "tip":
            await handle_party_tip(self, user, args)

        elif cmd == "transactions":
            await handle_transactions(self, user, args)

        elif cmd == "bankstats":
            await handle_bankstats(self, user)

        elif cmd == "banknotify":
            await handle_banknotify(self, user, args)

        elif cmd == "notifications":
            await handle_notifications(self, user, args)

        elif cmd == "clearnotifications":
            await handle_clearnotifications(self, user, args)

        elif cmd == "notifysettings":
            await handle_notifysettings(self, user, args)

        elif cmd == "notify":
            await handle_notify(self, user, args)

        elif cmd == "notifyhelp":
            await handle_notifyhelp(self, user, args)

        elif cmd == "delivernotifications":
            await handle_delivernotifications(self, user, args)

        elif cmd == "pendingnotifications":
            await handle_pendingnotifications(self, user, args)

        elif cmd in {"subscribe", "sub"}:
            await handle_subscribe(self, user, args)

        elif cmd in {"unsubscribe", "unsub"}:
            await handle_unsubscribe(self, user, args)

        elif cmd == "substatus":
            await handle_substatus(self, user, args)

        elif cmd == "subscribers":
            await handle_subscribers(self, user, args)

        elif cmd == "forcesub":
            await handle_forcesub(self, user, args)

        elif cmd == "fixsub":
            await handle_fixsub(self, user, args)

        elif cmd == "subhelp":
            await handle_subhelp(self, user, args)

        elif cmd == "bankhelp":
            await _handle_bankhelp(self, user, args)

        elif cmd == "casinohelp":
            await _handle_casinohelp(self, user, args)

        elif cmd == "gamehelp":
            await _handle_gamehelp(self, user, args)

        elif cmd == "coinhelp":
            _ch = should_this_bot_handle("coinhelp")
            print(f"[ROUTE] /coinhelp owner=banker current={BOT_MODE} handle={_ch}")
            await self.highrise.send_whisper(
                user.id,
                "💰 Coins\n"
                "!balance — show balance\n"
                "!daily — daily reward\n"
                "!send [user] [amount] — send coins\n"
                "!tip [user] [amount] — tip player"
            )

        elif cmd == "bankerhelp":
            print(f"[RX] mode={BOT_MODE} text=/bankerhelp")
            print(f"[ROUTE] /bankerhelp owner=banker current={BOT_MODE} handle=true")
            await _handle_bankerhelp(self, user, args)

        elif cmd == "economydbcheck":
            await _handle_economydbcheck(self, user)

        elif cmd == "economyrepair":
            await _handle_economyrepair(self, user)

        elif cmd == "profilehelp":
            await self.highrise.send_whisper(user.id, PROFILE_HELP)

        elif cmd == "shophelp":
            await _handle_shophelp(self, user, args)

        elif cmd in {"badgehelp", "helpbadge", "helpbadges"}:
            await handle_badge_help(self, user)

        elif cmd in {"helpmarket", "markethelp"}:
            await handle_market_help(self, user)

        elif cmd in {"helptrade", "tradehelp"}:
            await handle_trade_help(self, user)

        elif cmd == "progresshelp":
            await self.highrise.send_whisper(user.id, PROGRESS_HELP)

        elif cmd == "eventhelp":
            await _handle_eventhelp_paged(self, user, args)

        elif cmd == "bjhelp":
            await _handle_bjhelp(self, user, args)

        elif cmd == "blackjackhelp":
            await _handle_bjhelp(self, user, args)

        elif cmd == "bjstatus":
            await handle_bjstatus(self, user)

        elif cmd == "rbjhelp":
            await _handle_rbjhelp(self, user, args)

        elif cmd in {"bjcards", "blackjackcards", "cardmode", "bjcardmode"}:
            await handle_bj_cards(self, user, args)

        elif cmd == "bjrules":
            await handle_bj_rules(self, user)

        elif cmd in {"bjbonus", "bjbonussetting", "bjbonussettings"}:
            await handle_bj_bonus_settings(self, user)

        elif cmd == "rephelp":
            await _handle_rephelp(self, user)

        elif cmd == "autohelp":
            await _handle_autohelp(self, user)

        elif cmd == "vipstatus":
            await handle_vipstatus(self, user, args)

        elif cmd == "vip":
            await handle_vip(self, user, args)

        elif cmd == "vipperks":
            await handle_vipperks(self, user)

        elif cmd == "myvip":
            await handle_myvip(self, user)

        elif cmd == "giftvip":
            await handle_giftvip(self, user, args)

        elif cmd in {"viplist", "vips"}:
            await handle_viplist(self, user)

        elif cmd == "grantvip":
            await handle_grantvip(self, user, args)

        elif cmd in {"vipshop", "buyvip"}:
            await handle_buyvip(self, user, args)

        elif cmd in {"tickets", "luxe"}:
            await handle_tickets(self, user, args)

        elif cmd in {"luxeshop", "premiumshop"}:
            await handle_luxeshop(self, user, args)

        elif cmd in {"buyticket", "buyluxe"}:
            await handle_buyluxe(self, user, args)

        elif cmd == "buycoins":
            await handle_buycoins(self, user, args)

        elif cmd == "use":
            await handle_use_permit(self, user, args)

        elif cmd in {"autotime", "minetime", "fishtime"}:
            # minetime / fishtime pass category hint as first arg
            if cmd == "minetime":
                await handle_autotime(self, user, ["autotime", "mine"])
            elif cmd == "fishtime":
                await handle_autotime(self, user, ["autotime", "fish"])
            else:
                await handle_autotime(self, user, args)

        elif cmd == "autoconvert":
            await handle_autoconvert(self, user, args)

        elif cmd in {"tipadmin", "tipconfig"}:
            await handle_tipadmin(self, user, args)

        elif cmd == "economydefaults":
            await handle_economydefaults(self, user, args)

        elif cmd == "luxeadmin":
            await handle_luxeadmin(self, user, args)

        elif cmd == "vipadmin":
            await handle_vipadmin(self, user, args)

        elif cmd == "ticketrate":
            await handle_ticketrate(self, user)

        elif cmd == "donate":
            await handle_donate(self, user)

        elif cmd == "donationgoal":
            await handle_donationgoal(self, user)

        elif cmd in {"topdonors", "topdonators", "donators"}:
            await handle_topdonors(self, user)

        elif cmd == "sponsor":
            await handle_sponsor(self, user)

        elif cmd == "sponsorgoldrain":
            await handle_sponsorgoldrain(self, user)

        elif cmd == "sponsorevent":
            await handle_sponsorevent(self, user)

        elif cmd == "supporter":
            await handle_supporter(self, user)

        elif cmd == "perks":
            await handle_perks(self, user)

        elif cmd == "setdonationgoal":
            await handle_setdonationgoal(self, user, args)

        elif cmd == "donationaudit":
            await handle_donationaudit(self, user, args)

        elif cmd == "donationdebug":
            await handle_donationdebug(self, user, args)

        elif cmd == "setsponsorprice":
            await handle_setsponsorprice(self, user, args)

        elif cmd == "viphelp":
            await _handle_viphelp(self, user, args)

        elif cmd == "tiphelp":
            await _handle_tiphelp(self, user)

        elif cmd == "roleshelp":
            await _handle_roleshelp(self, user)

        elif cmd == "audithelp":
            await _handle_audithelp_cmd(self, user)

        elif cmd == "reporthelp":
            await _handle_reporthelp_cmd(self, user, args)

        elif cmd == "maintenancehelp":
            await _handle_maintenancehelp(self, user)

        elif cmd in {"commands", "cmds"}:
            await handle_commands_browser(self, user, args)

        elif cmd == "command":
            await handle_command_detail(self, user, args)

        elif cmd == "mycommands":
            await handle_mycommands(self, user, args)

        elif cmd == "helpsearch":
            await handle_helpsearch(self, user, args)

        # ── Player utility commands ────────────────────────────────────────────
        elif cmd == "menu":
            await handle_menu(self, user)

        elif cmd in {"cooldowns", "mycooldowns"}:
            await handle_cooldowns_cmd(self, user)

        elif cmd == "rewards":
            await handle_rewards_inbox(self, user)

        elif cmd == "wherebots":
            await handle_wherebots(self, user)

        elif cmd == "updates":
            await handle_updates(self, user)

        elif cmd == "rankup":
            await handle_rankup(self, user)

        elif cmd == "active":
            await handle_active(self, user)

        # ── Suggestions + bug reports ─────────────────────────────────────────
        elif cmd == "suggest":
            await handle_suggest(self, user, args)

        elif cmd == "suggestions":
            await handle_suggestions(self, user)

        elif cmd in {"bugreport", "reportbug"}:
            await handle_bugreport(self, user, args)

        elif cmd == "bugreports":
            await handle_bugreports(self, user)

        # ── Event vote ────────────────────────────────────────────────────────
        elif cmd in {"eventvote", "eventvotes"}:
            await handle_eventvote(self, user)

        elif cmd in {"voteevent", "votenextevent"}:
            await handle_voteevent(self, user, args)

        # ── Subscriber notification preferences ───────────────────────────────
        elif cmd in {"notif", "notifstatus"}:
            await handle_notif(self, user)

        elif cmd in {"notifon", "notifyon"}:
            await handle_notifon(self, user, args)

        elif cmd in {"notifoff", "notifyoff"}:
            await handle_notifoff(self, user, args)

        elif cmd == "notifpreview":
            await handle_notifpreview(self, user, args)

        elif cmd == "notifall":
            await handle_notifall(self, user, args)

        elif cmd in {"notifdm", "opennotifs"}:
            await handle_notifdm(self, user)

        elif cmd == "setsubnotifycooldown":
            await handle_setsubnotifycooldown(self, user, args)

        elif cmd in {"subnotify", "subnotif"}:
            await handle_subnotify(self, user, args)

        elif cmd == "subnotifyinvite":
            await handle_subnotifyinvite(self, user, args)

        elif cmd == "subnotifystatus":
            await handle_subnotifystatus(self, user, args)

        # ── Safe mode + diagnostics ───────────────────────────────────────────
        elif cmd == "safemode":
            await handle_safemode(self, user, args)

        elif cmd == "repair":
            await handle_repair(self, user)

        elif cmd in {"start", "begin", "newplayer"}:
            await handle_onboarding_start(self, user, args)

        elif cmd in {"guide", "whatdoido", "roomguide"}:
            await handle_onboarding_guide(self, user, args)

        elif cmd in {"new", "newbie"}:
            await handle_onboarding_newbie(self, user, args)

        elif cmd in {"tutorial", "newbiehelp"}:
            await handle_onboarding_tutorial(self, user, args)

        elif cmd in {"starter", "startermissions"}:
            await handle_onboarding_starter(self, user, args)

        elif cmd == "onboardadmin":
            await handle_onboardadmin(self, user, args)

        elif cmd == "activities":
            await self.highrise.send_whisper(user.id,
                "🎮 Activities\n"
                "⛏️ Mining — !mine\n"
                "🎣 Fishing — !fish\n"
                "🃏 Blackjack — !bet\n"
                "♠️ Poker — !poker\n"
                "🎲 Mini games — !help games\n"
                "🎉 Events — !events\n"
                "💰 Daily reward — !daily\n"
                "Use !help games to start."
            )

        elif cmd == "roominfo":
            await self.highrise.send_whisper(user.id,
                "🏠 ChillTopia\n"
                "A chill lounge with mining, fishing, casino games, "
                "mini games, VIP perks, teleports, and events.\n"
                "Start with !start."
            )

        elif cmd == "casinoadminhelp":
            await _handle_casinoadminhelp(self, user, args)

        elif cmd == "bankadminhelp":
            await _handle_bankadminhelp(self, user, args)

        elif cmd == "staffhelp":
            await _handle_staffhelp(self, user)

        elif cmd in ("modhelp", "mod"):
            await handle_modhelp_tiered(self, user, args)

        elif cmd == "managerhelp":
            await _handle_managerhelp(self, user, args)

        elif cmd == "adminhelp":
            await _handle_adminhelp(self, user, args)

        elif cmd == "ownerhelp":
            await _handle_ownerhelp(self, user, args)

        elif cmd == "goldhelp":
            await handle_goldhelp(self, user, args)

        elif cmd == "questhelp":
            await handle_questhelp(self, user)

        elif cmd == "quests":
            await handle_quests(self, user)

        elif cmd in {"missions", "dailymissions", "dailygoals"}:
            await handle_missions(self, user, args)
            asyncio.create_task(check_tutorial_step(self, user, "missions"))

        elif cmd in {"weekly", "weeklymissions", "weeklygoals"}:
            await handle_weekly_missions(self, user, args)

        elif cmd == "claimmission":
            await handle_claimmission(self, user, args)

        elif cmd == "claimdaily":
            await handle_claimdaily_missions(self, user, args)

        elif cmd == "claimweekly":
            await handle_claimweekly(self, user, args)

        elif cmd in {"milestones", "collectionrewards"}:
            await handle_milestones(self, user, args)

        elif cmd == "claimmilestone":
            await handle_claimmilestone(self, user, args)

        elif cmd in {"xp", "rank"}:
            await handle_mission_level(self, user, args)

        elif cmd == "season":
            await handle_season(self, user, args)

        elif cmd == "topseason":
            await handle_topseason(self, user, args)

        elif cmd == "seasonrewards":
            await handle_seasonrewards(self, user, args)

        elif cmd in {"today", "progress"}:
            await handle_today(self, user, args)
            asyncio.create_task(check_tutorial_step(self, user, "today"))

        elif cmd == "missionadmin":
            await handle_missionadmin(self, user, args)

        elif cmd == "retentionadmin":
            await handle_retentionadmin(self, user, args)

        elif cmd == "dailyquests":
            await handle_dailyquests(self, user)

        elif cmd == "weeklyquests":
            await handle_weeklyquests(self, user)

        elif cmd == "claimquest":
            await handle_claimquest(self, user, args)

        elif cmd in {"weeklylb", "weeklyleaderboard"}:
            await handle_weeklylb(self, user, args)

        elif cmd == "weeklyreset":
            await handle_weeklyreset(self, user, args)

        elif cmd == "weeklyrewards":
            await handle_weeklyrewards(self, user, args)

        elif cmd == "setweeklyreward":
            await handle_setweeklyreward(self, user, args)

        elif cmd == "weeklystatus":
            await handle_weeklystatus(self, user, args)

        elif cmd == "casino":
            _casino_known = {"modes", "on", "off", "reset", "leaderboard"}
            _casino_sub   = args[1].lower().lstrip("@") if len(args) > 1 else ""
            if not _casino_sub or _casino_sub.isdigit():
                await self.highrise.send_whisper(
                    user.id,
                    "🎰 ChillTopia Casino\n"
                    "Blackjack: !bjhelp\n"
                    "Poker: !pokerhelp\n"
                    "Balance: !balance\n"
                    "Start blackjack: !bet [amount]"
                )
            elif _casino_sub in _casino_known:
                await _handle_casino_cmd(self, user, args)
            else:
                await handle_casino_profile(self, user, args[1])

        elif cmd == "owners":
            await _cmd_owners(self, user)

        elif cmd == "managers":
            mgrs = db.get_managers()
            msg = "Managers: " + ", ".join(f"@{m}" for m in mgrs) if mgrs else "No managers set."
            await self.highrise.send_whisper(user.id, msg[:245])

        elif cmd == "moderators":
            mods = db.get_moderators()
            msg = "Moderators: " + ", ".join(f"@{m}" for m in mods) if mods else "No moderators set."
            await self.highrise.send_whisper(user.id, msg[:245])

        # ── 3.3A — ChillTopiaMC AI assistant (say "ai [question]" in chat) ─────
        elif cmd == "ai":
            await self.highrise.send_whisper(
                user.id,
                "💬 Talk to me by starting your message with 'ai'.\n"
                "Examples:\n"
                "• ai what should I do next?\n"
                "• ai explain Luxe Tickets\n"
                "• ai what date is today?\n"
                "• ai how do I mine?",
            )

        # ── /answer ───────────────────────────────────────────────────────────
        elif cmd == "answer":
            answer_text = " ".join(args[1:]).strip()
            if not answer_text:
                await self.highrise.send_whisper(user.id, "Usage: !answer <your answer>")
                return
            await games_handle_answer(self, user, answer_text)

        elif cmd in ("gamehint", "autogamehint"):
            await handle_gamehint(self, user)

        elif cmd in ("revealanswer", "revealgameanswer", "autogamereveal"):
            await handle_revealanswer(self, user)

        # ── Report commands ───────────────────────────────────────────────────
        elif cmd == "report":
            await handle_report(self, user, args)

        elif cmd == "bug":
            await handle_bug(self, user, args)

        elif cmd == "myreports":
            await handle_myreports(self, user)

        elif cmd == "reports":
            await handle_reports(self, user)

        elif cmd == "reportinfo":
            await handle_reportinfo(self, user, args)

        elif cmd == "closereport":
            await handle_closereport(self, user, args)

        elif cmd == "reportwatch":
            await handle_reportwatch(self, user, args)

        # ── Reputation commands ───────────────────────────────────────────────
        elif cmd == "rep":
            await handle_rep(self, user, args)

        elif cmd in {"reputation", "repstats"}:
            await handle_reputation(self, user)

        elif cmd in {"toprep", "repleaderboard"}:
            await handle_toprep(self, user)

        # ── Tip system ───────────────────────────────────────────────────────
        elif cmd == "tiprate":
            await handle_tiprate(self, user, args)

        elif cmd == "tipstats":
            await handle_tipstats(self, user, args)

        elif cmd == "tipleaderboard":
            await handle_tipleaderboard(self, user, args)

        # ── Poker V2 — player commands (ChipSoprano only) ────────────────────
        elif cmd == "join":
            await handle_poker_v2(self, user, "join", args)

        elif cmd in ("check", "ch"):
            await handle_poker_v2(self, user, "check", args)

        elif cmd in ("call", "ca"):
            await handle_poker_v2(self, user, "call", args)

        elif cmd in ("raise", "r"):
            await handle_poker_v2(self, user, "raise", args)

        elif cmd in ("fold", "f"):
            await handle_poker_v2(self, user, "fold", args)

        elif cmd in ("allin", "shove", "all-in"):
            await handle_poker_v2(self, user, "allin", args)

        elif cmd == "hand":
            await handle_poker_v2(self, user, "hand", args)

        elif cmd == "leave":
            await handle_poker_v2(self, user, "leave", args)

        elif cmd == "table":
            await handle_poker_v2(self, user, "table", args)

        # ── Poker — deprecated short aliases (redirect) ───────────────────────
        elif cmd in ("p", "pj"):
            # Old join aliases — if amount supplied, redirect to join syntax
            try:
                if len(args) >= 2 and args[1].replace(",", "").isdigit():
                    await self.highrise.send_whisper(
                        user.id, f"Use !join {args[1]} to join poker.")
                else:
                    await self.highrise.send_whisper(
                        user.id, "Use !join 5000 to join poker.")
            except Exception:
                pass

        elif cmd in ("pt", "ptable"):
            await handle_poker_v2(self, user, "table", args)

        elif cmd == "ph":
            await handle_poker_v2(self, user, "hand", args)

        elif cmd == "pleave":
            await handle_poker_v2(self, user, "leave", args)

        elif cmd in ("sitout",):
            await handle_poker_v2(self, user, "sitout", args)

        elif cmd in ("sitback", "sitin"):
            await handle_poker_v2(self, user, "sitback", args)

        elif cmd == "rebuy":
            await handle_poker_v2(self, user, "rebuy", args)

        elif cmd in ("lasthand", "handlog"):
            await handle_poker_v2(self, user, "lasthand", args)

        elif cmd in ("cards", "pcards"):
            await handle_poker_v2(self, user, "hand", args)

        elif cmd == "resendcards":
            await handle_poker_v2(self, user, "resendcards", args)

        elif cmd in ("po", "podds", "pp", "pplayers", "pstacks", "mystack"):
            try:
                await self.highrise.send_whisper(
                    user.id, "Use !table or !hand for poker info.")
            except Exception:
                pass

        # ── Poker — stats / leaderboard ───────────────────────────────────────
        elif cmd in ("pstats", "pokerstats"):
            await handle_pokerstats(self, user, args)

        elif cmd in ("plb", "pleaderboard", "pokerlb", "pokerleaderboard"):
            mode_args = args[1:] if len(args) >= 2 else []
            await handle_pokerlb(self, user, mode_args)

        elif cmd in ("phelp",):
            await handle_poker_v2(self, user, "poker", args)

        elif cmd == "pokerguide":
            await handle_poker_v2(self, user, "guide", args)

        # ── Poker — !poker command: all subcommands routed inside poker_v2 ───
        elif cmd == "poker":
            await handle_poker_v2(self, user, "poker", args)

        elif cmd == "pokerhelp":
            await handle_poker_v2(self, user, "poker", args)

        elif cmd == "pokerstatus":
            await handle_pokerstatus(self, user, args)

        elif cmd == "setpokerbuyin":
            await handle_setpokerbuyin(self, user, args)

        elif cmd == "setpokerplayers":
            await handle_setpokerplayers(self, user, args)

        elif cmd == "setpokerlobbytimer":
            await handle_setpokerlobbytimer(self, user, args)

        elif cmd == "setpokercardmarker":
            from modules.poker import handle_setpokercardmarker
            await handle_setpokercardmarker(self, user, args)

        elif cmd == "setpokertimer":
            await handle_setpokertimer(self, user, args)

        elif cmd == "setpokerraise":
            await handle_setpokerraise(self, user, args)

        elif cmd == "setpokerdailywinlimit":
            await handle_setpokerdailywinlimit(self, user, args)

        elif cmd == "setpokerdailylosslimit":
            await handle_setpokerdailylosslimit(self, user, args)

        elif cmd == "resetpokerlimits":
            await handle_resetpokerlimits(self, user, args)

        elif cmd == "pokermode":
            await handle_pokermode(self, user, args)

        elif cmd == "pokerpace":
            await handle_pokerpace(self, user)

        elif cmd == "setpokerpace":
            await handle_setpokerpace(self, user, args)

        elif cmd == "pokerstacks":
            await handle_pokerstacks(self, user)

        elif cmd == "setpokerstack":
            await handle_setpokerstack(self, user, args)

        elif cmd in ("dealstatus", "pokerdealstatus"):
            await handle_dealstatus(self, user)

        elif cmd == "pokerplayers":
            await handle_pokerplayers(self, user)

        elif cmd == "setpokerturntimer":
            await handle_setpokerturntimer(self, user, args)

        elif cmd == "setpokerlimits":
            await handle_setpokerlimits(self, user, args)

        elif cmd == "setpokerblinds":
            await handle_setpokerblinds(self, user, args)

        elif cmd == "setpokerante":
            await handle_setpokerante(self, user, args)

        elif cmd == "setpokernexthandtimer":
            await handle_setpokernexthandtimer(self, user, args)

        elif cmd == "setpokermaxstack":
            await handle_setpokermaxstack(self, user, args)

        elif cmd == "setpokeridlestrikes":
            await handle_setpokeridlestrikes(self, user, args)

        elif cmd == "pokerdebug":
            await handle_pokerdebug(self, user, args)

        elif cmd == "pokerfix":
            await handle_pokerfix(self, user, args)

        elif cmd == "pokerrefundall":
            await handle_pokerrefundall(self, user, args)

        elif cmd == "pokercleanup":
            await handle_pokercleanup(self, user, args)

        elif cmd in ("pokerdashboard", "pdash", "pokeradmin"):
            await handle_pokerdashboard(self, user)

        elif cmd == "pokerpause":
            await handle_pokerpause(self, user)

        elif cmd == "pokerresume":
            await handle_pokerresume(self, user)

        elif cmd == "pokerforceadvance":
            await handle_pokerforceadvance(self, user)

        elif cmd == "pokerforceresend":
            await handle_pokerforceresend(self, user)

        elif cmd == "pokerturn":
            await handle_pokerturn(self, user)

        elif cmd == "pokerpots":
            await handle_pokerpots(self, user)

        elif cmd == "pokeractions":
            await handle_pokeractions(self, user)

        elif cmd == "pokerstylepreview":
            await handle_pokerstylepreview(self, user)

        elif cmd == "pokerresetturn":
            await handle_pokerresetturn(self, user)

        elif cmd == "pokerresethand":
            await handle_pokerresethand(self, user)

        elif cmd == "pokerresettable":
            await handle_pokerresettable(self, user)

        elif cmd == "confirmclosepoker":
            await handle_confirmclosepoker(self, user, args)

        elif cmd == "casinointegrity":
            if not can_manage_games(user.username):
                await self.highrise.send_whisper(user.id, "Staff only.")
            else:
                sub = args[1].lower() if len(args) > 1 else ""
                from modules.casino_integrity import run_casino_integrity
                await run_casino_integrity(self, user, sub)

        elif cmd == "integritylogs":
            from modules.casino_integrity import handle_integritylogs
            await handle_integritylogs(self, user, args)

        elif cmd == "carddeliverycheck":
            if not can_manage_games(user.username):
                await self.highrise.send_whisper(user.id, "Staff only.")
            else:
                from modules.casino_integrity import run_carddelivery_check
                await run_carddelivery_check(self, user, args)

        # ── Mining game ───────────────────────────────────────────────────────
        elif cmd in {"mine", "m", "dig"}:
            await handle_mine(self, user)
            asyncio.create_task(check_tutorial_step(self, user, "mine"))

        elif cmd in {"tool", "pickaxe"}:
            await handle_tool(self, user)

        elif cmd in {"upgradetool", "upick"}:
            await handle_upgradetool(self, user)

        elif cmd in {"mineprofile", "mp", "minerank"}:
            await handle_mineprofile(self, user, args)

        elif cmd in {"mineinv", "ores"}:
            await handle_mineinv(self, user, args)

        elif cmd == "sellores":
            await handle_sellores(self, user)

        elif cmd == "sellore":
            await handle_sellore(self, user, args)

        elif cmd == "minelb":
            await handle_minelb(self, user, args)

        elif cmd == "mineshop":
            await handle_mineshop(self, user)

        elif cmd == "minebuy":
            await handle_minebuy(self, user, args)

        elif cmd == "useluckboost":
            await handle_useluckboost(self, user)

        elif cmd == "usexpboost":
            await handle_usexpboost(self, user)

        elif cmd == "useenergy":
            await handle_useenergy(self, user, args)

        elif cmd == "craft":
            await handle_craft(self, user, args)

        elif cmd == "minedaily":
            await handle_minedaily(self, user)

        elif cmd == "miningevent":
            await handle_miningevent(self, user)

        elif cmd == "miningevents":
            await handle_miningevents(self, user)

        elif cmd == "startminingevent":
            await handle_startminingevent(self, user, args)

        elif cmd == "stopminingevent":
            await handle_stopminingevent(self, user)

        elif cmd == "mining":
            await handle_mining_toggle(self, user, args)

        elif cmd == "miningadmin":
            await handle_miningadmin(self, user)

        elif cmd == "setminecooldown":
            await handle_setminecooldown(self, user, args)

        elif cmd == "setmineenergycost":
            await handle_setmineenergycost(self, user, args)

        elif cmd == "setminingenergy":
            await handle_setminingenergy(self, user, args)

        elif cmd == "addore":
            await handle_addore(self, user, args)

        elif cmd == "removeore":
            await handle_removeore(self, user, args)

        elif cmd == "settoollevel":
            await handle_settoollevel(self, user, args)

        elif cmd == "setminelevel":
            await handle_setminelevel(self, user, args)

        elif cmd == "addminexp":
            await handle_addminexp(self, user, args)

        elif cmd == "setminexp":
            await handle_setminexp(self, user, args)

        elif cmd == "resetmining":
            await handle_resetmining(self, user, args)

        elif cmd == "miningroomrequired":
            await handle_miningroomrequired(self, user, args)

        elif cmd == "mineconfig":
            await handle_mineconfig(self, user)

        elif cmd == "mineeventstatus":
            await handle_mineeventstatus(self, user)

        elif cmd in {"minepanel", "miningpanel"}:
            await handle_minepanel(self, user)

        elif cmd == "mineadmin":
            await handle_mineadmin(self, user, args)

        elif cmd == "orelist":
            await handle_orelist(self, user, args)

        elif cmd in {"oreprices", "orevalues", "orevalue"}:
            await handle_oreprices(self, user, args)

        elif cmd in {"oreinfo", "oredetail", "oredetails"}:
            await handle_oreinfo(self, user, args)

        elif cmd == "simannounce":
            await handle_simannounce(self, user, args)

        elif cmd == "forcedrop":
            await handle_forcedrop(self, user, args)

        elif cmd == "forcedropore":
            await handle_forcedropore(self, user, args)

        elif cmd == "forcedropstatus":
            await handle_forcedropstatus(self, user)

        elif cmd == "clearforcedrop":
            await handle_clearforcedrop(self, user, args)

        # ── Fishing force drop (owner-only) ───────────────────────────────────
        elif cmd in {"forcedropfish", "forcefishdrop", "forcefish"}:
            await handle_forcedropfish(self, user, args)

        elif cmd in {"forcedropfishitem", "forcefishdropfish"}:
            await handle_forcedropfishitem(self, user, args)

        elif cmd in {"forcedropfishstatus", "forcefishstatus"}:
            await handle_forcedropfishstatus(self, user)

        elif cmd == "forcedropfishdebug":
            await handle_forcedropfishdebug(self, user, args)

        elif cmd in {"clearforcedropfish", "clearforcefish"}:
            await handle_clearforcedropfish(self, user, args)

        elif cmd == "clearforceddropfish":
            await self.highrise.send_whisper(
                user.id,
                "❓ Did you mean /clearforcedropfish? (one 'd', not two)"
            )

        elif cmd == "minechances":
            await handle_minechances(self, user)

        elif cmd in {"fishchances", "fishingchances"}:
            await handle_fishchances(self, user)

        elif cmd == "raritychances":
            # Show both mining and fishing summary
            await handle_minechances(self, user)
            await handle_fishchances(self, user)

        elif cmd == "orechances":
            await handle_orechances(self, user)

        elif cmd == "orechance":
            await handle_orechance(self, user, args)

        elif cmd == "setorechance":
            await handle_setorechance(self, user, args)

        elif cmd == "setraritychance":
            await handle_setraritychance(self, user, args)

        elif cmd == "reloadorechances":
            await handle_reloadorechances(self, user)

        elif cmd in {"automine", "am"}:
            if len(args) >= 2 and args[1].lower() == "1h":
                await handle_use_permit(self, user, ["use", "automine1h"])
            else:
                await handle_automine(self, user, args)

        elif cmd in {"autominestatus", "amstatus"}:
            await handle_autominestatus(self, user)

        elif cmd == "autominesettings":
            await handle_autominesettings(self, user)

        elif cmd in {"mineluck", "minestack"}:
            await handle_mineluck(self, user, args)

        elif cmd == "setautomine":
            await handle_setautomine(self, user, args)

        elif cmd == "setautomineduration":
            await handle_setautomineduration(self, user, args)

        elif cmd == "setautomineattempts":
            await handle_setautomineattempts(self, user, args)

        elif cmd == "setautominedailycap":
            await handle_setautominedailycap(self, user, args)

        elif cmd in {"economypanel", "economybalance", "miningeconomy"}:
            await handle_economypanel(self, user)

        elif cmd == "economysettings":
            await handle_economysettings(self, user)

        elif cmd in {"economycap", "economycaps"}:
            await handle_economycap(self, user)

        elif cmd == "setraritycap":
            await handle_setraritycap(self, user, args)

        elif cmd == "resetraritycaps":
            await handle_resetraritycaps(self, user)

        elif cmd in {"payoutlogs", "minepayoutlogs"}:
            await handle_payoutlogs(self, user, args)

        elif cmd == "biggestpayouts":
            await handle_biggestpayouts(self, user)

        # ── Fishing commands ─────────────────────────────────────────────────
        elif cmd in {"fish", "cast", "reel"}:
            await handle_fish(self, user)
            asyncio.create_task(check_tutorial_step(self, user, "fish"))

        elif cmd in {"fishlist", "fishrarity", "fishes"}:
            await handle_fishlist(self, user, args)

        elif cmd in {"fishprices", "fishvalues"}:
            await handle_fishprices(self, user, args)

        elif cmd in {"fishinfo", "fishdetail"}:
            await handle_fishinfo(self, user, args)

        elif cmd in {"myfish", "fishinv", "fishbag", "fishinventory"}:
            await handle_myfish(self, user)

        elif cmd == "sellfish":
            await handle_sellfish(self, user)

        elif cmd == "sellallfish":
            await handle_sellallfish(self, user)

        elif cmd in {"sellfishrarity", "sellrarity"}:
            await handle_sellfishrarity(self, user, args)

        elif cmd == "fishbook":
            await handle_fishbook(self, user, args)

        elif cmd in {"topfishcollectors", "fishcollectors"}:
            await handle_topcollectors(self, user, ["topcollectors", "fish"])

        elif cmd == "lastfishsummary":
            await handle_lastfishsummary(self, user, args)

        elif cmd in {"fishautosell", "autosellfish"}:
            await handle_fishautosell(self, user, args)

        elif cmd in {"fishautosellrare", "autosellrare"}:
            await handle_fishautosellrare(self, user, args)

        elif cmd in {"fishlevel", "fishxp", "fishlvl"}:
            await handle_fishlevel(self, user)

        elif cmd == "fishstats":
            await handle_fishstats(self, user, args)

        elif cmd in {"fishboosts", "fishingevents"}:
            await handle_fishboosts(self, user)

        elif cmd in {"fishhelp", "fishinghelp"}:
            await handle_fishhelp(self, user)

        elif cmd in {"topfish", "topfishing", "fishlb"}:
            await handle_topfish(self, user)

        elif cmd in {"topweightfish", "biggestfish", "heaviestfish"}:
            await handle_topweightfish(self, user)

        elif cmd in {"rods", "rod", "fishroads", "listfishrods"}:
            await handle_rods(self, user)

        elif cmd in {"myrod", "equippedrod"}:
            await handle_myrod(self, user)

        elif cmd in {"rodshop", "fishrodshop"}:
            await handle_rodshop(self, user)

        elif cmd in {"buyrod", "purchaserod"}:
            await handle_buyrod(self, user, args)

        elif cmd in {"equiprod", "switchrod"}:
            await handle_equiprod(self, user, args)

        elif cmd in {"rodinfo", "roddetail"}:
            await handle_rodinfo(self, user, args)

        elif cmd == "rodstats":
            await handle_rodstats(self, user)

        elif cmd == "rodupgrade":
            await handle_rodupgrade(self, user)

        elif cmd in {"autofish", "af"}:
            if len(args) >= 2 and args[1].lower() == "1h":
                await handle_use_permit(self, user, ["use", "autofish1h"])
            else:
                await handle_autofish(self, user, args)

        elif cmd in {"autofishstatus", "afstatus"}:
            await handle_autofishstatus(self, user)

        elif cmd == "autofishsettings":
            await handle_autofishsettings(self, user)

        elif cmd in {"fishluck", "fishstack"}:
            await handle_fishluck(self, user, args)

        elif cmd == "fishadmin":
            await handle_fishadmin(self, user, args)

        elif cmd in {"fishpanel", "fishingpanel"}:
            await handle_fishpanel(self, user)

        elif cmd == "setfishcooldown":
            await handle_setfishcooldown(self, user, args)

        elif cmd == "setfishweights":
            await handle_setfishweights(self, user, args)

        elif cmd == "setfishweightscale":
            await handle_setfishweightscale(self, user, args)

        elif cmd == "setfishannounce":
            await handle_setfishannounce(self, user, args)

        elif cmd == "setfishrarityweightrange":
            await handle_setfishrarityweightrange(self, user, args)

        elif cmd == "boostadmin":
            await handle_boostadmin(self, user, args)

        elif cmd in {"luck", "myluck"}:
            from modules.luck_stack import get_mine_luck_stack, get_fish_luck_stack
            from modules.events import get_event_effect as _luck_gee
            _ms  = get_mine_luck_stack(user.id, user.username)
            _fs  = get_fish_luck_stack(user.id, user.username)
            _eff = _luck_gee()
            _xp_line = (f"\n⭐ XP Event: {_eff['xp']:.0f}x active"
                        if _eff.get("xp", 1.0) > 1.0 else "")
            _ev_m = f" (+{_ms['event_luck']} event)" if _ms["event_luck"] else ""
            _ev_f = f" (+{_fs['event_luck']} event)" if _fs["event_luck"] else ""
            await self.highrise.send_whisper(
                user.id,
                (f"🍀 Your Luck\n"
                 f"⛏️ {_ms['luck_total']} luck{_ev_m} | {_ms['interval_secs']}s/mine\n"
                 f"🎣 {_fs['luck_total']} luck{_ev_f} | {_fs['interval_secs']}s/cast"
                 f"{_xp_line}\n"
                 f"!mineluck or !fishluck for details.")[:249])

        elif cmd == "setautofish":
            await handle_setautofish(self, user, args)

        elif cmd == "setautofishduration":
            await handle_setautofishduration(self, user, args)

        elif cmd == "setautofishattempts":
            await handle_setautofishattempts(self, user, args)

        elif cmd == "setautofishdailycap":
            await handle_setautofishdailycap(self, user, args)

        # ── First-find race commands ───────────────────────────────────────
        elif cmd in {"firstfindrewards", "firstfindlist", "firstfindreward"}:
            await handle_firstfindrewards(self, user)

        elif cmd in {"setfirstfind", "setfirstfindreward"}:
            await handle_setfirstfind(self, user, args)

        elif cmd in {"setfirstfinditem"}:
            await handle_setfirstfinditem(self, user, args)

        elif cmd in {"startfirstfind"}:
            await handle_startfirstfind(self, user, args)

        elif cmd in {"stopfirstfind"}:
            await handle_stopfirstfind(self, user)

        elif cmd in {"firstfindstatus", "firstfindcheck"}:
            await handle_firstfindstatus(self, user, args)

        elif cmd in {"firstfindwinners"}:
            await handle_firstfindwinners(self, user)

        elif cmd == "resetfirstfind":
            await handle_resetfirstfind(self, user, args)

        elif cmd in {"firstfindpending", "firstfindpay"}:
            await handle_firstfindpending(self, user)

        elif cmd in {"paypendingfirstfind", "retryfirstfind"}:
            await handle_paypendingfirstfind(self, user, args)

        # ── Reward center commands ─────────────────────────────────────────
        elif cmd in {"rewardpending", "pendingrewards"}:
            await handle_rewardpending(self, user, args)

        elif cmd == "rewardlogs":
            await handle_rewardlogs(self, user, args)

        elif cmd == "markrewardpaid":
            await handle_markrewardpaid(self, user, args)

        elif cmd == "economyreport":
            await handle_economyreport(self, user, args)

        # ── Big announce commands ──────────────────────────────────────────
        elif cmd == "setbigannounce":
            await handle_setbigannounce(self, user, args)

        elif cmd in {"setbigreact", "setbotbigreact"}:
            await handle_setbotbigreact(self, user, args)

        elif cmd == "bigannouncestatus":
            await handle_bigannouncestatus(self, user)

        elif cmd == "bigannounce":
            await handle_bigannounce_help(self, user)

        elif cmd == "previewannounce":
            await handle_previewannounce(self, user, args)

        elif cmd == "orebook":
            await handle_orebook(self, user, args)

        elif cmd in {"collection", "mybook", "collectbook"}:
            await handle_collection(self, user, args)

        elif cmd in {"enabledm", "summarydm"}:
            await handle_enabledm(self, user, args)

        elif cmd in {"collectionhelp", "bookhelp"}:
            await handle_collectionhelp(self, user, args)

        elif cmd == "rarelog":
            await handle_rarelog(self, user, args)

        elif cmd == "lastminesummary":
            await handle_lastminesummary(self, user, args)

        elif cmd in {"topcollectors", "topore", "toporecollectors"}:
            if cmd == "topore":
                await handle_topcollectors(self, user, ["topcollectors", "ore"])
            else:
                await handle_topcollectors(self, user, args)

        elif cmd == "oremastery":
            await handle_oremastery(self, user)

        elif cmd == "claimoremastery":
            await handle_claimoremastery(self, user, args)

        elif cmd == "orestats":
            await handle_orestats(self, user, args)

        elif cmd in {"contracts", "miningjobs"}:
            await handle_contracts(self, user)

        elif cmd == "job":
            await handle_job(self, user, args)

        elif cmd == "deliver":
            await handle_deliver(self, user, args)

        elif cmd == "claimjob":
            await handle_claimjob(self, user)

        elif cmd == "rerolljob":
            await handle_rerolljob(self, user)

        elif cmd == "minehelp":
            await handle_minehelp(self, user, args)

        elif cmd in {"oreweightlb", "weightlb", "heaviest"}:
            await handle_oreweightlb(self, user, args)

        elif cmd == "myheaviest":
            await handle_myheaviest(self, user, args)

        elif cmd == "oreweights":
            await handle_oreweights(self, user)

        elif cmd == "topweights":
            await handle_topweights(self, user)

        elif cmd == "setweightlbmode":
            await handle_setweightlbmode(self, user, args)

        elif cmd == "mineannounce":
            await handle_mineannounce(self, user)

        elif cmd == "setmineannounce":
            await handle_setmineannounce(self, user, args)

        elif cmd == "setoreannounce":
            await handle_setoreannounce(self, user, args)

        elif cmd == "oreannounce":
            await handle_oreannounce(self, user, args)

        elif cmd == "mineannouncesettings":
            await handle_mineannouncesettings(self, user)

        elif cmd == "mineweights":
            await handle_mineweights(self, user)

        elif cmd == "setmineweights":
            await handle_setmineweights(self, user, args)

        elif cmd == "setweightscale":
            await handle_setweightscale(self, user, args)

        elif cmd == "setrarityweightrange":
            await handle_setrarityweightrange(self, user, args)

        elif cmd == "oreweightsettings":
            await handle_oreweightsettings(self, user)

        # ── Per-bot welcome messages ───────────────────────────────────────────
        elif cmd == "botwelcome":
            await handle_botwelcome(self, user)
        elif cmd == "setbotwelcome":
            await handle_setbotwelcome(self, user, args)
        elif cmd == "resetbotwelcome":
            await handle_resetbotwelcome(self, user, args)
        elif cmd == "previewbotwelcome":
            await handle_previewbotwelcome(self, user, args)
        elif cmd == "botwelcomes":
            await handle_botwelcomes(self, user, args)

        # ── Bio + onboarding audit (3.0C) ─────────────────────────────────────
        elif cmd == "bios":
            await handle_bios(self, user)
        elif cmd == "checkbios":
            await handle_checkbios(self, user)
        elif cmd == "checkonboarding":
            await handle_checkonboarding(self, user)

        # ── Gold tip commands ─────────────────────────────────────────────────
        elif cmd == "goldtipsettings":
            await handle_goldtipsettings(self, user)
        elif cmd == "setgoldrate":
            await handle_setgoldrate(self, user, args)
        elif cmd == "goldtiplogs":
            await handle_goldtiplogs(self, user)
        elif cmd == "mygoldtips":
            await handle_mygoldtips(self, user)
        elif cmd == "goldtipstatus":
            await handle_goldtipstatus(self, user)
        elif cmd == "tipcoinrate":
            await handle_tipcoinrate(self, user)
        elif cmd == "settipcoinrate":
            await handle_settipcoinrate(self, user, args)
        elif cmd == "bottiplogs":
            await handle_bottiplogs(self, user)
        elif cmd == "mingoldtip":
            await handle_mingoldtip(self, user)
        elif cmd == "setmingoldtip":
            await handle_setmingoldtip(self, user, args)
        elif cmd in ("tiplb", "tipleaderboard", "bottiplb", "bottipleaderboard"):
            await handle_tiplb(self, user, args)
        elif cmd in ("roomtiplb", "roomtipleaderboard", "alltiplb", "alltipleaderboard"):
            await handle_roomtiplb(self, user)
        elif cmd in ("tipreceiverlb", "topreceivers"):
            await handle_tipreceiverlb(self, user)

        # ── Notification / room debug ─────────────────────────────────────────
        elif cmd == "notifydebug":
            await handle_notifydebug(self, user, args)

        elif cmd == "roomusers":
            await handle_roomusers(self, user, args)

        elif cmd == "testwhisper":
            await handle_testwhisper(self, user, args)

        elif cmd == "notifrefresh":
            await handle_notifrefresh(self, user, args)

        # ── QoL / player support ──────────────────────────────────────────────
        elif cmd == "quicktest":
            await handle_quicktest(self, user)

        elif cmd == "playercheck":
            await handle_playercheck(self, user, args)

        elif cmd == "claimrewards":
            await handle_claimrewards(self, user)

        elif cmd in {"eventcalendar", "calendar"}:
            await handle_eventcalendar(self, user)

        elif cmd == "lastupdate":
            await handle_lastupdate(self, user, args)

        elif cmd in {"knownissues", "issues"}:
            await handle_knownissues(self, user)

        elif cmd == "knownissue":
            await handle_knownissue(self, user, args)

        elif cmd == "feedback":
            await handle_feedback(self, user, args)

        elif cmd in {"feedbacks", "feedbacklist"}:
            if len(args) > 1:
                await handle_feedbacks_review(self, user, args)
            else:
                await handle_feedbacks(self, user)

        elif cmd == "todo":
            await handle_todo(self, user, args)

        elif cmd == "aetest":
            await handle_aetest(self, user)

        elif cmd == "ownercheck":
            await handle_ownercheck(self, user)

        # ── Maintenance tools ─────────────────────────────────────────────────
        elif cmd == "botstatus":
            await handle_botstatus_simple(self, user, args)

        elif cmd == "dbstats":
            await handle_dbstats(self, user)

        elif cmd == "maintenance":
            await handle_maintenance(self, user, args)

        # ── 3.1Q — Beta/launch tools ──────────────────────────────────────────
        elif cmd == "betamode":
            await handle_betamode(self, user, args)

        elif cmd == "betacheck":
            await handle_betacheck(self, user)

        elif cmd == "betadash":
            await handle_betadash(self, user)

        elif cmd == "issueadmin":
            await handle_issueadmin(self, user, args)

        elif cmd == "bugs":
            await handle_bugs_admin(self, user, args)

        elif cmd == "errors":
            await handle_errors_admin(self, user, args)

        elif cmd == "launchready":
            await handle_launchready(self, user, args)

        elif cmd == "announceadmin":
            await handle_announceadmin(self, user, args)

        # ── 3.1S — Release Candidate + Production Lock ───────────────────────
        elif cmd == "rcmode":
            await handle_rcmode(self, user, args)

        elif cmd == "production":
            await handle_production(self, user, args)

        elif cmd == "featurefreeze":
            await handle_featurefreeze(self, user, args)

        elif cmd == "economylock":
            await handle_economylock(self, user, args)

        elif cmd == "registrylock":
            await handle_registrylock(self, user, args)

        elif cmd == "releasenotes":
            await handle_releasenotes(self, user, args)

        elif cmd in {"version", "botversion"}:
            await handle_version(self, user, args)

        elif cmd == "backup":
            await handle_backup(self, user, args)

        elif cmd == "rollbackplan":
            await handle_rollbackplan(self, user, args)

        elif cmd == "restorebackup":
            await handle_restorebackup(self, user, args)

        elif cmd in {"ownerchecklist", "launchownercheck"}:
            await handle_ownerchecklist(self, user, args)

        elif cmd == "launchannounce":
            await handle_launchannounce(self, user, args)

        elif cmd in {"whatsnew", "new", "v32"}:
            await handle_whatsnew(self, user, args)

        elif cmd == "knownissues":
            await handle_knownissues(self, user, args)

        elif cmd == "releasedash":
            await handle_releasedash(self, user, args)

        elif cmd == "finalaudit":
            await handle_finalaudit(self, user, args)

        # ── 3.2J — Owner QA + Stable Lock ─────────────────────────────────────
        elif cmd in {"qastatus", "ownerqa"}:
            await handle_qastatus(self, user, args)

        elif cmd == "ownertest":
            await handle_ownertest(self, user, args)

        elif cmd == "stablecheck":
            await handle_stablecheck(self, user, args)

        elif cmd == "hotfixpolicy":
            await handle_hotfixpolicy(self, user, args)

        elif cmd == "stablelock":
            await handle_stablelock(self, user, args)

        # ── 3.2A — Public Launch + Post-Launch Monitoring ─────────────────────
        elif cmd == "launchmode":
            await handle_launchmode(self, user, args)

        elif cmd == "postlaunch":
            await handle_postlaunch(self, user, args)

        elif cmd == "livehealth":
            await handle_livehealth(self, user, args)

        elif cmd == "bugdash":
            await handle_bugdash(self, user, args)

        elif cmd == "feedbackdash":
            await handle_feedbackdash(self, user, args)

        elif cmd == "dailyreview":
            await handle_dailyreview(self, user, args)

        elif cmd == "economymonitor":
            await handle_economymonitor(self, user, args)

        elif cmd == "luxemonitor":
            await handle_luxemonitor(self, user, args)

        elif cmd == "retentionmonitor":
            await handle_retentionmonitor(self, user, args)

        elif cmd == "eventmonitor":
            await handle_eventmonitor(self, user, args)

        elif cmd == "casinomonitor":
            await handle_casinomonitor(self, user, args)

        elif cmd == "bjmonitor":
            await handle_bjmonitor(self, user, args)

        elif cmd == "pokermonitor":
            await handle_pokermonitor(self, user, args)

        elif cmd == "errordash":
            await handle_errordash(self, user, args)

        elif cmd == "hotfix":
            await handle_hotfix(self, user, args)

        elif cmd == "hotfixlog":
            await handle_hotfixlog(self, user, args)

        elif cmd == "launchlocks":
            await handle_launchlocks(self, user, args)

        elif cmd == "snapshot":
            await handle_snapshot(self, user, args)

        # ── 3.1R — Beta review + balance audit ───────────────────────────────
        elif cmd == "betatest":
            await handle_betatest(self, user, args)

        elif cmd == "topissues":
            await handle_topissues(self, user, args)

        elif cmd == "balanceaudit":
            await handle_balanceaudit(self, user, args)

        elif cmd == "livebalance":
            await handle_livebalance(self, user, args)

        elif cmd == "luxebalance":
            await handle_luxebalance(self, user, args)

        elif cmd == "retentionreview":
            await handle_retentionreview(self, user, args)

        elif cmd == "eventreview":
            await handle_eventreview(self, user, args)

        elif cmd == "seasonreview":
            await handle_seasonreview(self, user)

        elif cmd == "funnel":
            await handle_funnel(self, user, args)

        elif cmd == "betareport":
            await handle_betareport(self, user, args)

        elif cmd == "launchblockers":
            await handle_launchblockers(self, user)

        elif cmd == "betastaff":
            await handle_betastaff(self, user, args)

        # ── 3.1Q — Public beta commands ───────────────────────────────────────
        elif cmd in {"testmenu", "betahelp"}:
            await handle_betahelp(self, user)

        elif cmd == "quickstart":
            await handle_quickstart(self, user)

        elif cmd == "reloadsettings":
            await handle_reloadsettings(self, user)

        elif cmd == "cleanup":
            await handle_cleanup(self, user)

        # ── Game commands ─────────────────────────────────────────────────────
        elif cmd in GAME_COMMANDS:
            await handle_game_command(self, user, cmd, args)

        # ── Room utility — player lists ───────────────────────────────────────
        elif cmd in {"players", "roomlist", "online"}:
            await handle_players(self, user)
        elif cmd == "staffonline":
            await handle_staffonline(self, user)
        elif cmd == "vipsinroom":
            await handle_vipsinroom(self, user)
        elif cmd == "rolelist":
            await handle_rolelist(self, user, args)

        # ── Spawns ────────────────────────────────────────────────────────────
        elif cmd == "spawns":
            await handle_spawns(self, user)
        elif cmd == "spawn":
            await handle_spawn(self, user, args)
        elif cmd == "setspawn":
            await handle_setspawn(self, user, args)
        elif cmd == "savepos":
            await handle_savepos(self, user, args)
        elif cmd == "delspawn":
            await handle_delspawn(self, user, args)
        elif cmd == "spawninfo":
            await handle_spawninfo(self, user, args)
        elif cmd == "setspawncoords":
            await handle_setspawncoords(self, user, args)

        # ── Bot spawns ────────────────────────────────────────────────────────
        elif cmd == "setbotspawn":
            await handle_setbotspawn(self, user, args)
        elif cmd == "setbotspawnhere":
            await handle_setbotspawnhere(self, user, args)
        elif cmd == "botspawns":
            await handle_botspawns(self, user)
        elif cmd == "clearbotspawn":
            await handle_clearbotspawn(self, user, args)
        elif cmd in ("returnbots", "botshome"):
            await handle_returnbots(self, user, args)
        elif cmd == "botspawn" and len(args) >= 2 and args[1].lower() in ("return", "returnall"):
            await handle_returnbots(self, user, args)
        elif cmd == "mypos":
            await handle_mypos(self, user, args)
        elif cmd == "positiondebug":
            await handle_positiondebug(self, user, args)

        # ── Tele / tag / role-spawn system (!tele, !create tele, etc.) ──────────
        elif cmd == "tele":
            await handle_tele(self, user, args)
        elif cmd == "create" and len(args) >= 3 and args[1].lower() == "tele":
            await handle_create_tele(self, user, args)
        elif cmd == "delete" and len(args) >= 3 and args[1].lower() == "tele":
            await handle_delete_tele(self, user, args)
        elif cmd == "summon":
            await handle_summon(self, user, args)
        elif cmd == "tag":
            await handle_tag(self, user, args)
        elif cmd == "setrolespawn":
            await handle_setrolespawn(self, user, args)
        elif cmd == "rolespawn":
            await handle_rolespawn(self, user, args)
        elif cmd == "rolespawns":
            await handle_rolespawns(self, user)
        elif cmd == "delrolespawn":
            await handle_delrolespawn(self, user, args)
        elif cmd == "autospawn":
            await handle_autospawn(self, user, args)
        elif cmd == "roles":
            await handle_roles(self, user, args)
        elif cmd == "rolemembers":
            await handle_rolemembers(self, user, args)

        # ── Party Tip Wallet (ChillTopiaMC) ──────────────────────────────────
        elif cmd == "party":
            await handle_party(self, user, args)
        elif cmd == "pton":
            await handle_pton(self, user)
        elif cmd == "ptoff":
            await handle_ptoff(self, user)
        elif cmd == "ptstatus":
            await handle_ptstatus(self, user)
        elif cmd == "tipall":
            await handle_tipall_redirect(self, user)
        elif cmd == "ptenable":
            await handle_ptenable(self, user)
        elif cmd == "ptdisable":
            await handle_ptdisable(self, user)
        elif cmd == "stability":
            await handle_stability(self, user, args)
        elif cmd in ("ptwallet", "partywallet", "setpartywallet"):
            await handle_ptwallet(self, user, args)
        elif cmd in ("ptadd", "partytipperadd"):
            await handle_ptadd(self, user, args)
        elif cmd in ("ptremove", "partytipperremove"):
            await handle_ptremove(self, user, args)
        elif cmd in ("ptclear", "partytipperclear"):
            await handle_ptclear(self, user)
        elif cmd in ("ptlist", "partytippers"):
            await handle_ptlist(self, user)
        elif cmd in ("ptlimits", "partytipperlimits"):
            await handle_ptlimits(self, user)
        elif cmd in ("ptlimit", "setpartytipperlimit"):
            await handle_ptlimit(self, user, args)
        elif cmd == "partytipper":
            # !partytipper add|remove|clear [user] [args]
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "add":
                await handle_ptadd(self, user, ["ptadd"] + args[2:])
            elif sub == "remove":
                await handle_ptremove(self, user, ["ptremove"] + args[2:])
            elif sub == "clear":
                await handle_ptclear(self, user)
            else:
                await handle_ptlist(self, user)

        # ── Teleport ──────────────────────────────────────────────────────────
        elif cmd == "tpme":
            await handle_tpme(self, user, args)
        elif cmd == "tp":
            await handle_tp(self, user, args)
        elif cmd in {"tphere", "bring"}:
            await handle_tphere(self, user, args)
        elif cmd == "goto":
            await handle_goto(self, user, args)
        elif cmd == "bringall":
            await handle_bringall(self, user)
        elif cmd == "tpall":
            await handle_tpall(self, user, args)
        elif cmd == "tprole":
            await handle_tprole(self, user, args)
        elif cmd == "tpvip":
            await handle_tpvip(self, user, args)
        elif cmd == "tpstaff":
            await handle_tpstaff(self, user, args)
        elif cmd == "selftp":
            await handle_selftp(self, user, args)
        elif cmd == "groupteleport":
            await handle_groupteleport(self, user, args)

        # ── Emotes ────────────────────────────────────────────────────────────
        elif cmd == "emotes":
            await handle_emotes(self, user, args)
        elif cmd == "emoteinfo":
            await handle_emoteinfo(self, user, args)
        elif cmd == "emote":
            await handle_emote(self, user, args)
        elif cmd == "stopemote":
            await handle_stopemote(self, user)
        elif cmd == "dance":
            await handle_dance(self, user)
        elif cmd == "wave":
            await handle_wave(self, user)
        elif cmd == "sit":
            await handle_sit(self, user)
        elif cmd == "clap":
            await handle_clap(self, user)
        elif cmd == "forceemote":
            await handle_forceemote(self, user, args)
        elif cmd == "forceemoteall":
            await handle_forceemoteall(self, user, args)
        elif cmd == "loopemote":
            await handle_loopemote(self, user, args)
        elif cmd == "stoploop":
            await handle_stoploop(self, user, args)
        elif cmd == "stopallloops":
            await handle_stopallloops(self, user)
        elif cmd in {"synchost", "syncdance"}:
            await handle_synchost(self, user, args)
        elif cmd == "stopsync":
            await handle_stopsync(self, user)
        elif cmd == "publicemotes":
            await handle_publicemotes(self, user, args)
        elif cmd == "forceemotes":
            await handle_forceemotes(self, user, args)
        elif cmd == "setemoteloopinterval":
            await handle_setemoteloopinterval(self, user, args)

        # ── Hearts ────────────────────────────────────────────────────────────
        elif cmd in {"heart", "giveheart", "reactheart"}:
            await handle_heart(self, user, args)
        elif cmd == "hearts":
            await handle_hearts(self, user, args)
        elif cmd == "heartlb":
            await handle_heartlb(self, user)

        # ── Social actions ────────────────────────────────────────────────────
        elif cmd == "hug":
            await handle_hug(self, user, args)
        elif cmd == "kiss":
            await handle_kiss(self, user, args)
        elif cmd == "slap":
            await handle_slap(self, user, args)
        elif cmd == "punch":
            await handle_punch(self, user, args)
        elif cmd == "highfive":
            await handle_highfive(self, user, args)
        elif cmd == "boop":
            await handle_boop(self, user, args)
        elif cmd == "waveat":
            await handle_waveat(self, user, args)
        elif cmd == "cheer":
            await handle_cheer(self, user, args)
        elif cmd == "social":
            await handle_social(self, user, args)
        elif cmd == "blocksocial":
            await handle_blocksocial(self, user, args)
        elif cmd == "unblocksocial":
            await handle_unblocksocial(self, user, args)
        elif cmd == "socialhelp":
            await handle_socialhelp(self, user)

        # ── Bot follow ────────────────────────────────────────────────────────
        elif cmd == "followme":
            await handle_followme(self, user)
        elif cmd == "follow":
            await handle_follow(self, user, args)
        elif cmd == "stopfollow":
            await handle_stopfollow(self, user)
        elif cmd == "followstatus":
            await handle_followstatus(self, user)

        # ── Alerts ────────────────────────────────────────────────────────────
        elif cmd == "alert":
            await handle_alert(self, user, args)
        elif cmd in {"roomalert"}:
            await handle_alert(self, user, args)
        elif cmd == "staffalert":
            await handle_staffalert(self, user, args)
        elif cmd == "vipalert":
            await handle_vipalert(self, user, args)
        elif cmd == "clearalerts":
            await handle_clearalerts(self, user)

        # ── Welcome ───────────────────────────────────────────────────────────
        elif cmd == "welcome":
            await handle_welcome(self, user, args)
        elif cmd == "setwelcome":
            await handle_setwelcome(self, user, args)
        elif cmd == "welcometest":
            await handle_welcometest(self, user)
        elif cmd == "resetwelcome":
            await handle_resetwelcome(self, user, args)
        elif cmd == "welcomeinterval":
            await handle_welcomeinterval(self, user, args)

        # ── Interval messages ─────────────────────────────────────────────────
        elif cmd == "intervals":
            await handle_intervals(self, user)
        elif cmd == "addinterval":
            await handle_addinterval(self, user, args)
        elif cmd == "delinterval":
            await handle_delinterval(self, user, args)
        elif cmd == "interval":
            await handle_interval(self, user, args)
        elif cmd == "intervaltest":
            await handle_intervaltest(self, user, args)

        # ── Repeat message ────────────────────────────────────────────────────
        elif cmd == "repeatmsg":
            await handle_repeatmsg(self, user, args)
        elif cmd == "stoprepeat":
            await handle_stoprepeat(self, user)
        elif cmd == "repeatstatus":
            await handle_repeatstatus(self, user)

        # ── Room settings & logs ──────────────────────────────────────────────
        elif cmd == "roomsettings":
            await handle_roomsettings(self, user, args)
        elif cmd == "setroomsetting":
            await handle_setroomsetting(self, user, args)
        elif cmd == "roomlogs":
            await handle_roomlogs(self, user, args)

        # ── Extended moderation ───────────────────────────────────────────────
        elif cmd == "kick":
            await handle_kick(self, user, args)
        elif cmd == "ban":
            await handle_ban(self, user, args)
        elif cmd == "tempban":
            await handle_tempban(self, user, args)
        elif cmd == "unban":
            await handle_unban(self, user, args)
        elif cmd == "bans":
            await handle_bans(self, user)
        elif cmd == "modlog":
            await handle_modlog(self, user, args)
        elif cmd in ("safetyadmin", "antispam", "spamadmin"):
            await handle_safetyadmin(self, user, args)
        elif cmd in ("economysafety", "txsafety", "economyalerts"):
            await handle_economysafety(self, user, args)
        elif cmd in ("safetydash", "safedash", "safetydashboard"):
            await handle_safetydash(self, user)

        # ── 3.1O Analytics dashboards ──────────────────────────────────────────
        elif cmd in ("ownerdash", "dashboard", "analytics"):
            await handle_ownerdash(self, user, args)
        elif cmd in ("playerstats",):
            await handle_playerstats(self, user, args)
        elif cmd in ("economydash",):
            await handle_economydash(self, user, args)
        elif cmd in ("luxedash",):
            await handle_luxedash(self, user, args)
        elif cmd in ("conversiondash",):
            await handle_conversiondash(self, user, args)
        elif cmd in ("minedash",):
            await handle_minedash(self, user, args)
        elif cmd in ("fishdash",):
            await handle_fishdash(self, user, args)
        elif cmd in ("activitydash",):
            await handle_activitydash(self, user, args)
        elif cmd in ("hourlyaudit",):
            await handle_hourlyaudit(self, user, args)
        elif cmd in ("shopdash",):
            await handle_shopdash(self, user, args)
        elif cmd in ("vipdash",):
            await handle_vipdash(self, user, args)
        elif cmd in ("retentiondash",):
            await handle_retentiondash(self, user, args)
        elif cmd in ("eventdash",):
            await handle_eventdash(self, user, args)
        elif cmd in ("seasondash",):
            await handle_seasondash(self, user, args)
        elif cmd in ("analyticsdash", "analyticshelp"):
            await handle_analyticsdash(self, user, args)

        # ── Boost / mic ───────────────────────────────────────────────────────
        elif cmd in {"boostroom", "roomboost"}:
            await handle_boostroom(self, user)
        elif cmd in {"startmic", "micstart"}:
            await handle_startmic(self, user)
        elif cmd == "micstatus":
            await handle_micstatus(self, user)

        # ── Bot modes ─────────────────────────────────────────────────────────
        elif cmd == "botmode":
            await handle_botmode(self, user, args)
        elif cmd == "botmodes":
            await handle_botmodes(self, user)
        elif cmd == "botprofile":
            await handle_botprofile(self, user)
        elif cmd == "botprefix":
            await handle_botprefix(self, user, args)
        elif cmd == "categoryprefix":
            await handle_categoryprefix(self, user, args)
        elif cmd == "setbotprefix":
            await handle_setbotprefix(self, user, args)
        elif cmd == "setbotdesc":
            await handle_setbotdesc(self, user, args)
        elif cmd == "setbotoutfit":
            await handle_setbotoutfit(self, user, args)
        elif cmd == "botoutfit":
            await handle_botoutfit(self, user, args)
        elif cmd in ("botoutfits", "botoutfitstatus"):
            await handle_botoutfits(self, user, args)
        elif cmd == "dressbot":
            await handle_dressbot(self, user, args)
        elif cmd == "savebotoutfit":
            await handle_savebotoutfit(self, user, args)
        elif cmd == "copyoutfit":
            await handle_copyoutfit(self, user, args)
        elif cmd == "wearuseroutfit":
            await handle_wearuseroutfit(self, user, args)
        elif cmd == "copymyoutfit":
            await handle_copymyoutfit(self, user, args)
        elif cmd == "copyoutfitfrom":
            await handle_copyoutfitfrom(self, user, args)
        elif cmd == "savemyoutfit":
            await handle_savemyoutfit(self, user, args)
        elif cmd == "wearoutfit":
            await handle_wearoutfit(self, user, args)
        elif cmd == "myoutfits":
            await handle_myoutfits(self, user, args)
        elif cmd == "myoutfitstatus":
            await handle_myoutfitstatus(self, user, args)
        elif cmd == "directoutfittest":
            await handle_directoutfittest(self, user, args)

        elif cmd in ("botoutfitdebug", "outfitdebug"):
            await handle_botoutfitdebug(self, user, args)
        elif cmd == "renamebotoutfit":
            await handle_renamebotoutfit(self, user, args)
        elif cmd == "clearbotoutfit":
            await handle_clearbotoutfit(self, user, args)
        elif cmd == "createbotmode":
            await handle_createbotmode(self, user, args)
        elif cmd == "deletebotmode":
            await handle_deletebotmode(self, user, args)
        elif cmd == "assignbotmode":
            await handle_assignbotmode(self, user, args)
        elif cmd == "bots":
            await handle_bots_live(self, user)
        elif cmd == "botinfo":
            await handle_botinfo(self, user, args)
        elif cmd == "botoutfitlogs":
            await handle_botoutfitlogs(self, user)
        elif cmd == "botmodehelp":
            await handle_botmodehelp(self, user)

        # ── Room help pages ───────────────────────────────────────────────────
        elif cmd == "roomhelp":
            await handle_roomhelp(self, user)
        elif cmd == "teleporthelp":
            await handle_teleporthelp(self, user)
        elif cmd == "emotehelp":
            await handle_emotehelp(self, user)
        elif cmd == "alerthelp":
            await handle_alerthelp(self, user)
        elif cmd == "welcomehelp":
            await handle_welcomehelp(self, user)

        # ── Multi-bot system ─────────────────────────────────────────────────
        elif cmd == "botmodules":
            await handle_botmodules(self, user)
        elif cmd == "commandowners":
            await handle_commandowners(self, user)
        elif cmd == "enablebot":
            await handle_enablebot(self, user, args)
        elif cmd == "disablebot":
            await handle_disablebot(self, user, args)
        elif cmd == "setbotmodule":
            await handle_setbotmodule(self, user, args)
        elif cmd == "setcommandowner":
            await handle_setcommandowner(self, user, args)
        elif cmd == "botfallback":
            await handle_botfallback(self, user, args)
        elif cmd == "botstartupannounce":
            await handle_botstartupannounce(self, user, args)
        elif cmd == "startupannounce":
            await handle_startupannounce(self, user, args)
        elif cmd == "modulestartup":
            await handle_modulestartup(self, user, args)
        elif cmd == "startupstatus":
            await handle_startupstatus(self, user)
        elif cmd == "multibothelp":
            await handle_multibothelp(self, user, args)
        elif cmd == "setmainmode":
            await handle_setmainmode(self, user, args)

        # ── Bot health / deployment checks ─────────────────────────────────────
        elif cmd == "bothealth":
            await handle_bothealth(self, user, args)
        elif cmd == "modulehealth":
            await handle_modulehealth(self, user, args)
        elif cmd == "deploymentcheck":
            await handle_deploymentcheck(self, user, args)
        elif cmd == "dblockcheck":
            await handle_dblockcheck(self, user, args)
        elif cmd == "botlocks":
            await handle_botlocks(self, user)
        elif cmd == "clearstalebotlocks":
            await handle_clearstalebotlocks(self, user)
        elif cmd == "botheartbeat":
            await handle_botheartbeat(self, user)
        elif cmd == "moduleowners":
            await handle_moduleowners(self, user, args)
        elif cmd == "botconflicts":
            await handle_botconflicts(self, user)
        elif cmd == "fixbotowners":
            await handle_fixbotowners(self, user, args)

        # ── Task ownership / restore announce ─────────────────────────────────
        elif cmd == "taskowners":
            await handle_taskowners(self, user)
        elif cmd == "activetasks":
            await handle_activetasks(self, user)
        elif cmd == "taskconflicts":
            await handle_taskconflicts(self, user)
        elif cmd == "fixtaskowners":
            await handle_fixtaskowners(self, user)
        elif cmd == "restoreannounce":
            await handle_restoreannounce(self, user, args)
        elif cmd == "restorestatus":
            await handle_restorestatus(self, user)

        # ── Control panel ─────────────────────────────────────────────────────
        elif cmd == "control":
            await handle_control(self, user, args)
        elif cmd == "ownerpanel":
            await handle_ownerpanel(self, user)
        elif cmd == "economystatus":
            await handle_economystatus(self, user)
        elif cmd == "managerpanel":
            await handle_managerpanel(self, user)
        elif cmd == "status":
            await handle_status(self, user)
        elif cmd == "roomstatus":
            await handle_roomstatus(self, user)
        elif cmd == "quicktoggles":
            await handle_quicktoggles(self, user)
        elif cmd == "toggle":
            await handle_toggle(self, user, args)

        # ── Diagnostics — lightweight info commands ────────────────────────────
        elif cmd == "crashlogs":
            await self.highrise.send_whisper(
                user.id, "No crash log system in this version."
            )

        elif cmd == "missingbots":
            _mb_instances = db.get_bot_instances()
            if not _mb_instances:
                await self.highrise.send_whisper(
                    user.id, "Missing bots: none registered (single-mode)."
                )
            else:
                from datetime import datetime as _dt, timezone as _tz
                _mb_now = _dt.now(_tz.utc)
                _mb_miss: list[str] = []
                for _mbi in _mb_instances:
                    if not _mbi.get("enabled", 1):
                        continue
                    _mbm = _mbi.get("bot_mode", "?")
                    _mbts = _mbi.get("last_seen_at", "")
                    if not _mbts:
                        _mb_miss.append(_mbm)
                        continue
                    try:
                        _mbls = _dt.fromisoformat(_mbts.replace("Z", "+00:00"))
                        if _mbls.tzinfo is None:
                            _mbls = _mbls.replace(tzinfo=_tz.utc)
                        _mbage = (_mb_now - _mbls).total_seconds()
                        if _mbage > 120:
                            _mb_miss.append(f"{_mbm}(~{int(_mbage)}s)")
                    except Exception:
                        _mb_miss.append(f"{_mbm}(?)")
                _mb_msg = (("Missing: " + ", ".join(_mb_miss))
                           if _mb_miss else "Missing bots: none — all online.")
                await self.highrise.send_whisper(user.id, _mb_msg[:249])

        elif cmd == "commandaudit":
            await handle_commandaudit(self, user, args)

        # ── Unknown command — only host/all mode replies; others ignore silently
        else:
            if BOT_MODE not in ("host", "all"):
                return
            if cmd.startswith("gold") and not can_manage_economy(user.username):
                await self.highrise.send_whisper(user.id, "Gold commands are staff only.")
            elif cmd.startswith("vip"):
                await self.highrise.send_whisper(user.id,
                    "⚠️ Unknown VIP command. Try !viphelp.")
            elif cmd.startswith("poker") or cmd in {"pp", "pj", "pt", "ph", "po"}:
                await self.highrise.send_whisper(user.id,
                    "⚠️ Unknown poker command. Try !phelp.")
            elif cmd.startswith("bj") or cmd in {"bjoin", "bh", "bs", "bd", "bsp", "bt"}:
                await self.highrise.send_whisper(user.id,
                    "⚠️ Unknown BJ command. Try !bjhelp.")
            elif cmd.startswith("rbj") or cmd in {"rjoin", "rh", "rs", "rd", "rsp", "rt"}:
                await self.highrise.send_whisper(user.id,
                    "⚠️ Unknown RBJ command. Try !rbjhelp.")
            else:
                await handle_unknown_command(self, user, cmd)

    async def on_user_join(self, user: User, position) -> None:
        """Register new players and greet them when they enter the room."""
        try:
            db.ensure_user(user.id, user.username)
            add_to_room_cache(user.id, user.username)
            time_exp_record_join(user.id)
        except Exception as _e:
            print(f"[ON_JOIN ERROR] db/cache for @{user.username}: {_e!r}")
        # Update position cache for follow/teleport
        try:
            from highrise.models import Position as _Pos
            if isinstance(position, _Pos):
                update_user_position(user.id, position)
        except Exception:
            pass

        def _sj(coro, label):
            async def _g():
                try:
                    await coro
                except Exception as _e2:
                    print(f"[ON_JOIN TASK ERROR] {label} @{user.username}: {_e2!r}")
            return asyncio.create_task(_g())

        _sj(send_welcome_if_needed(self, user), "send_welcome_if_needed")
        # Jail re-enforcement on rejoin — SecurityBot only
        if should_this_bot_run_module("jail"):
            _sj(enforce_jail_on_rejoin(self, user.id, user.username), "jail_rejoin")
        # on_join_welcome: run only from host bot to prevent multi-bot spam
        if should_this_bot_run_module("timeexp"):
            _sj(on_join_welcome(self, user), "on_join_welcome")
        _sj(send_bot_welcome(
            self, user, get_bot_username() or BOT_MODE, stagger_seconds=2.0
        ), "send_bot_welcome")
        _sj(_deliver_pending_bank_notifications(self, user), "bank_notif")
        _sj(deliver_pending_subscriber_messages(self, user.username.lower()), "sub_notif")
        _sj(deliver_pending_notifications(self, user.username.lower()), "typed_notif")
        _sj(_autospawn_user_on_join(self, user), "autospawn")

    async def on_tip(self, sender: User, receiver: User, tip) -> None:
        """
        Official Highrise SDK tip handler.
        Maps to the 'tip_reaction' WebSocket event.
        SDK: on_tip(sender, receiver, tip: CurrencyItem | Item)

        If this NEVER prints, Highrise is not delivering tip_reaction to the bot.
        Nothing here is echoed to room chat.
        """
        # ABSOLUTE FIRST LINE — no try/except wrapping, runs unconditionally
        print("DEBUG EVENT FIRED: on_tip")
        print(f"  sender:    {sender.username} ({sender.id})")
        print(f"  receiver:  {receiver.username} ({receiver.id})")
        print(f"  tip raw:   {tip!r}")
        print(f"  tip class: {type(tip).__name__}")
        print(f"  tip.type:  {getattr(tip, 'type',   '?')}")
        print(f"  tip.amount:{getattr(tip, 'amount', '?')}")
        print(f"  tip.id:    {getattr(tip, 'id',     'n/a')}")

        record_debug_any_event("on_tip", repr(tip))

        # Only process tips directed at this bot
        bot_uid = get_bot_user_id()
        if bot_uid and receiver.id != bot_uid:
            print(
                f"  [TIP] Receiver {receiver.username}({receiver.id}) "
                f"!= bot ({bot_uid}) — P2P tip."
            )
            # Log P2P gold tips for leaderboards — banker only to avoid duplicates
            if BOT_MODE == "banker":
                try:
                    gold = extract_gold_from_tip(tip)
                    if gold and gold > 0:
                        _bf = db._get_bot_name_filter()
                        if (sender.username.lower() not in _bf
                                and receiver.username.lower() not in _bf):
                            import hashlib as _hl, time as _pt
                            _eid = _hl.md5(
                                f"p2p|{sender.id}|{receiver.id}|{gold}"
                                f"|{int(_pt.time())//10}".encode()
                            ).hexdigest()[:20]
                            _ins = db.record_p2p_gold_tip(
                                sender.id, sender.username,
                                receiver.id, receiver.username,
                                gold, _eid,
                            )
                            print(f"  [P2P] @{sender.username}→@{receiver.username} "
                                  f"{gold}g ({'logged' if _ins else 'dup'})")
                        else:
                            print(f"  [P2P] Skipped — bot in chain "
                                  f"({sender.username}→{receiver.username})")
                    else:
                        print(f"  [P2P] Non-gold: type={getattr(tip,'type','?')}")
                except Exception as _p2p_e:
                    print(f"  [P2P] Log error: {_p2p_e!r}")
            return

        # Route gold (non-coin) tips to the gold tip handler
        tip_type = getattr(tip, 'type', '')
        if str(tip_type).lower() == 'gold':
            gold_amount = float(getattr(tip, 'amount', 1))
            print(f"  [TIP] Gold tip detected: {gold_amount} gold from @{sender.username}")
            asyncio.create_task(handle_incoming_gold_tip(
                self, sender, receiver.username, gold_amount,
            ))
            return

        try:
            await process_tip_event(self, sender, receiver, tip)
        except Exception as exc:
            print(f"  [TIP] EXCEPTION in process_tip_event: {exc!r}")

    async def on_user_move(self, user: User, position) -> None:
        """Update position cache for teleport / follow features."""
        try:
            from highrise.models import Position as _Pos
            if isinstance(position, _Pos):
                update_user_position(user.id, position)
        except Exception:
            pass

    async def on_user_leave(self, user: User) -> None:
        """Log when a player leaves, remove from cache, stop AutoMine/AutoFish."""
        try:
            remove_from_room_cache(user.id)
            time_exp_record_leave(user.id)
            print(f"[HangoutBot] {user.username} left.")
        except Exception as _e:
            print(f"[ON_LEAVE ERROR] cache for @{user.username}: {_e!r}")
        try:
            stop_automine_for_user(user.id, user.username, "player_left")
        except Exception:
            pass
        try:
            stop_autofish_for_user(user.id, user.username, "player_left")
        except Exception:
            pass
        try:
            await handle_poker_user_left(self, user)
        except Exception as _pe:
            print(f"[ON_LEAVE POKER] @{user.username}: {_pe!r}")

    async def on_reaction(self, user: User, reaction: str, receiver: User) -> None:
        """
        Debug hook — subscribed so the Highrise server sends reaction events.
        Logs every reaction so we can see if gold tips arrive here instead of on_tip.
        """
        try:
            raw = f"reaction={reaction!r} from=@{user.username}({user.id}) to=@{receiver.username}({receiver.id})"
            print(f"DEBUG EVENT FIRED: on_reaction | {raw}")
            record_debug_any_event("on_reaction", raw)
        except Exception as _e:
            print(f"[ON_REACTION ERROR] {_e!r}")

    async def on_channel(self, sender_id: str, message: str, tags: set) -> None:
        """
        Cross-bot channel event handler.
        Routes notif_dispatch payloads to the subscriber notification system.
        Also logs gold/tip/coin-related messages for debugging.
        """
        raw = f"sender_id={sender_id} tags={tags} message={message[:80]!r}"
        record_debug_any_event("on_channel", raw)
        msg_lower = message.lower()
        if any(kw in msg_lower for kw in ("gold", "tip", "coin", "pay", "send", "reward")):
            print(f"DEBUG EVENT FIRED: on_channel | {raw}")
        # Big announce cross-bot channel
        if "big_announce" in msg_lower:
            print(f"[BIG_ANNOUNCE] channel event: {raw}")
        # Subscriber notification dispatch routing
        if "notif_dispatch" in msg_lower:
            try:
                import json as _json
                payload = _json.loads(message)
                if payload.get("type") == "notif_dispatch":
                    asyncio.create_task(handle_notif_dispatch_channel(self, payload))
            except Exception as _e:
                print(f"[SUB_NOTIF] on_channel parse error: {_e}")

    async def on_emote(self, user: User, emote_id: str, receiver) -> None:
        """
        Debug hook — overriding BaseBot adds 'emote' to subscriptions.
        Logs emotes silently; only prints if emote ID looks tip-related.
        """
        try:
            time_exp_record_activity(user.id)
            raw = f"user=@{user.username}({user.id}) emote_id={emote_id!r} receiver={receiver!r}"
            record_debug_any_event("on_emote", raw)
            if "tip" in emote_id.lower() or "gold" in emote_id.lower():
                print(f"DEBUG EVENT FIRED: on_emote | {raw}")
        except Exception as _e:
            print(f"[ON_EMOTE ERROR] {_e!r}")

    async def on_message(self, user_id: str, conversation_id: str, is_new_conversation: bool) -> None:
        """
        Fires when the bot receives a Highrise inbox/DM message.
        Saves the conversation_id and processes subscribe/unsubscribe keywords.

        NOTE: The MessageEvent carries no message text — we must call get_messages.
        A short sleep avoids a race condition where the new message hasn't been
        indexed on the platform yet when we fetch the history.
        """
        try:
            print(f"[DM] EVENT RECEIVED user_id={user_id[:12]}... conv={conversation_id[:12]}... new={is_new_conversation}")
            # Wait briefly so the platform indexes the new message before we fetch
            await asyncio.sleep(0.5)
            resp = await self.highrise.get_messages(conversation_id)
            messages = getattr(resp, "messages", []) or []

            content = ""
            if messages:
                # Messages are oldest-first; the last entry is the one that triggered
                # this event. Prefer it directly — fall back to sender_id scan if needed.
                last_msg = messages[-1]
                if getattr(last_msg, "sender_id", None) == user_id:
                    content = getattr(last_msg, "content", "").strip()
                else:
                    # Race or ordering issue — scan newest-first for this user's message
                    for msg in reversed(messages):
                        if getattr(msg, "sender_id", None) == user_id:
                            content = getattr(msg, "content", "").strip()
                            break

            print(f"[DM] content={content[:60]!r} (fetched {len(messages)} msgs)")
            await process_incoming_dm(self, user_id, conversation_id, content, is_new_conversation)
        except Exception as exc:
            print(f"[DM] on_message error: {exc}")

    async def on_whisper(self, user: User, message: str) -> None:
        """
        Dedicated whisper handler.

        Routes slash-commands to the owning bot handler if this bot owns them.
        Gives a short routing hint (60 s cooldown) when a command belongs to a
        different bot.  Also auto-subscribes the whispering player to
        notifications (if tip_auto_sub is ON and they haven't manually unsubbed).
        """
        raw = f"from=@{user.username}({user.id}) message={message[:60]!r}"
        record_debug_any_event("on_whisper", raw)
        msg_lower = message.lower()
        if any(kw in msg_lower for kw in ("gold", "tip", "coin")):
            print(f"DEBUG EVENT FIRED: on_whisper | {raw}")

        # ── Slash-command routing via whisper ─────────────────────────────────
        stripped = message.strip()
        if stripped.startswith("/") or stripped.startswith("!"):
            from modules.multi_bot import should_this_bot_handle, _resolve_command_owner
            cmd_word = stripped.split()[0][1:].lower()
            if should_this_bot_handle(cmd_word):
                print(f"[WHISPER] Routing /{cmd_word} for @{user.username}")
                try:
                    await self.on_chat(user, stripped)
                except Exception as exc:
                    print(f"[WHISPER] dispatch error for /{cmd_word}: {exc!r}")
                    await self.highrise.send_whisper(
                        user.id, "❌ Something went wrong handling that command.")
            else:
                owner_mode = _resolve_command_owner(cmd_word)
                if owner_mode:
                    now = asyncio.get_event_loop().time()
                    last = _whisper_wrong_bot_ts.get(user.id, 0.0)
                    if now - last >= 60:
                        _whisper_wrong_bot_ts[user.id] = now
                        hint = (f"❌ /{cmd_word} belongs to the {owner_mode} bot. "
                                "Try it in the room instead.")
                        await self.highrise.send_whisper(user.id, hint[:249])
                        print(f"[WHISPER] @{user.username} wrong-bot hint: "
                              f"/{cmd_word} → {owner_mode}")
                    else:
                        print(f"[WHISPER] @{user.username} wrong-bot hint suppressed (cooldown).")
            return  # Don't auto-subscribe when user is sending commands

        # Auto-subscribe from whisper is disabled — subscription is intentional only.

        # Room assistant — non-command whispers answered by whisper (host bot only)
        if BOT_MODE in ("host", "all"):
            try:
                await handle_room_assistant_whisper(self, user, message)
            except Exception as _ra_err:
                print(f"[ROOM_ASSIST] whisper handler error: {_ra_err!r}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run():
    """Connect the bot to Highrise and start the event loop."""
    import signal as _signal

    async def _main():
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(
            highrise_main([BotDefinition(
                bot=HangoutBot(),
                room_id=config.ROOM_ID,
                api_token=config.BOT_TOKEN,
            )])
        )

        def _shutdown(sig: int) -> None:
            print(f"[SHUTDOWN] {_signal.Signals(sig).name} — stopping bot...")
            task.cancel()

        try:
            loop.add_signal_handler(_signal.SIGTERM, _shutdown, _signal.SIGTERM)
            loop.add_signal_handler(_signal.SIGINT,  _shutdown, _signal.SIGINT)
        except (NotImplementedError, OSError):
            pass  # Windows / restricted env fallback

        try:
            await task
        except asyncio.CancelledError:
            print("[SHUTDOWN] Bot stopped cleanly.")

    asyncio.run(_main())


if __name__ == "__main__":
    run()
