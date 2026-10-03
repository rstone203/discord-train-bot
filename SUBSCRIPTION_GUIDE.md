# 📋 Subscription System Guide

## 🎯 **Per-User Subscription Model Explained**

### How It Works

The subscription system is **per-user**, NOT per-server. This means:

- ✅ Each user who wants to **create/manage** trains needs their own subscription
- ✅ Once subscribed, the user gets access across **ALL servers** with the bot
- ✅ **Participants** (people joining trains) don't need subscriptions
- ✅ **Trusted Users** (added via `/addtrusted`) can bypass subscriptions in their specific server

### Permission Hierarchy

**From highest to lowest priority:**
1. **Bot Owner** → Unlimited access everywhere, no subscription needed
2. **Trusted Users** → Can create trains in servers where they're trusted (via `/addtrusted`)
3. **Subscribers** → Can create trains in ALL servers
4. **Regular Users** → Can join trains but cannot create them (FREE)

### Example Scenario

**Server:** Game Lounge  
**Members:**
- **Alice** (has subscription) → Can create, edit, delete trains ✅
- **Bob** (no subscription) → Cannot create trains ❌, but can join them ✅
- **Charlie** (has subscription) → Can also create trains ✅

**Result:**
- Alice creates "Saturday Morning Train" → Success!
- Bob tries to create "Sunday Evening Train" → Blocked (needs subscription)
- Charlie creates "Monday Night Train" → Success!
- Bob joins Alice's train → Success! (joining is FREE)

---

## 🆓 **What's Always FREE**

### For Everyone (No Subscription Needed):
1. **Joining Trains**
   - `/jointrain` - Join any train session
   - `/leavetrain` - Leave a train
   - `/mytrains` - View your signed up trains
   - `/jointraindropdown` - Interactive join menu
   - `/leavetraindropdown` - Interactive leave menu

2. **Viewing Information**
   - `/trainroster` - See who's in a train
   - `/timeslots` - View all available time slots
   - View train schedules

3. **Message Forwarding**
   - All message forwarding features remain free

4. **Basic Commands**
   - `/ping`, `/help`, `/info`, etc.

---

## 🔒 **What Requires Subscription**

### Train Management (Organizers Only):
1. **Schedule Creation**
   - `!setuptrainschedule` - Create train schedules
   - `!createschedule` - Interactive schedule creator
   - `!setupweekschedule` - Create weekly schedules
   - `!trainconfig` - Configure train settings

2. **Persistent Displays**
   - `/setuppersistenttimeslots` - Auto-updating train display
   - `/removepersistenttimeslots` - Remove persistent displays

3. **Google Sheets Integration**
   - `/sheetsync` - Enable Google Sheets sync
   - `/sheetsyncsaturday` - Sync weekend schedules
   - `/sheetsunsync` - Disable sync
   - `/sheetstatus` - Check sync status
   - `/sheetsyncnow` - Manual sync trigger

4. **Analytics & Statistics**
   - All analytics commands
   - Participation stats
   - Session reports

5. **Twitch Integration**
   - Live role management
   - Stream analytics
   - Twitch chat monitoring

---

## ❓ **Common Questions**

### Q: "Multiple people in my server want to create trains - do they each need a subscription?"

**A:** Yes. Each person who wants to **create or manage** trains needs their own subscription. Think of it like this:
- Organizers (subscription needed) = Can create events
- Participants (FREE) = Can join events

**Example:**
- Your server has 50 members
- 3 people want to organize trains → 3 subscriptions needed
- Other 47 people just want to join trains → No subscriptions needed

### Q: "How do I know if someone in my server already has a subscription?"

**A:** Currently, subscriptions are private. However:
- Anyone who can successfully create/edit trains has a subscription
- If someone tries to create a train and gets blocked, they need to subscribe
- The bot owner (you) can see subscription stats with `/substats`

**Future Feature:** We could add a `/subscribers` command to show who in your server has subscriptions (if they opt-in to be visible).

### Q: "What if I subscribe in one server - does it work everywhere?"

**A:** Yes! Subscriptions follow the **user**, not the server. If you subscribe:
- ✅ You can create trains in Server A
- ✅ You can create trains in Server B
- ✅ You can create trains in ANY server with this bot

This is why the DM notification includes which server someone was in when they subscribed - they get access everywhere, but you'll know where they started.

### Q: "Can I see who has subscriptions in my server?"

**A:** As the bot owner, you can:
1. Use `/substats` to see total subscription counts
2. Check the logs to see who successfully creates trains (they have subscriptions)
3. When someone subscribes, you get a DM with their info and which server they were in

**Privacy Note:** Regular users can't see who else has subscriptions to protect privacy.

### Q: "If I add someone as a trusted user via `/addtrusted`, can they create trains without a subscription?"

**A:** **Yes!** Trusted users bypass the subscription requirement, but only in the specific server where they're trusted.

**Example:**
- You add **Bob** as a trusted user in **Game Lounge** server
- Bob can create trains in Game Lounge ✅ (no subscription needed)
- Bob CANNOT create trains in **Other Server** ❌ (would need subscription)

**Why this makes sense:**
- ✅ Server admins can designate "staff organizers" without requiring them to pay
- ✅ Gives you flexibility to have trusted helpers in specific servers
- ✅ Trusted users are server-specific (limited scope)
- ✅ Subscriptions work across ALL servers (broader access)

**Who Should Subscribe?**
- Users who want to create trains in **multiple servers**
- Users who aren't trusted in any server but want organizer access
- Public users who want full access without admin approval

---

## 💰 **Pricing Structure**

When enabled, the subscription cost is:
- **$5/month per user**
- Includes access to ALL features (except forwarding, which is always free)
- Works across ALL servers with the bot
- One-time subscription = unlimited train creation

**Your Revenue Math:**
- 5 subscribers = $25/month revenue
- Covers most of your $40/month hosting cost
- Remaining $15 can come from donations

---

## 🔐 **Owner Privileges**

As the bot owner, you:
- ✅ Have **unlimited access** to all features (no subscription needed)
- ✅ Can toggle the subscription system on/off with `/togglesubscriptions`
- ✅ Get DM notifications when anyone subscribes/cancels
- ✅ Can view subscription stats with `/substats`
- ✅ Can manually grant access if needed (future feature)

---

## 📊 **DM Notifications You'll Receive**

When someone subscribes, you get a DM like this:

```
💳 Subscription Subscribed

👤 User
JohnDoe#1234 (ID: 123456789)

🏠 Server
Game Lounge

✅ Event
Subscribed

📊 Details
Status: active
Subscription ID: sub_1234567890abcde...

📈 Current Stats
Active Subs: 5 | Total: 8
```

**You'll also be notified for:**
- ❌ Cancellations
- ⚠️ Payment failures
- 🔄 Status changes

---

## 🚀 **Current Status**

**System:** ✅ Built and ready  
**Status:** 🔒 **DISABLED** (all features currently FREE)  
**Ready to enable:** Yes, whenever you're ready

**To enable:**
1. Set up Stripe account
2. Create subscription product ($5/month)
3. Run `/togglesubscriptions enable <price_id>`

---

## 🎯 **Recommended Communication Strategy**

When you enable subscriptions, here's how to communicate it to your community:

**Message Template:**
```
🚂 **Train Bot Update - New Subscription Model**

Hey everyone! To keep the bot running 24/7, we're introducing subscriptions for train organizers.

💰 **What's FREE Forever:**
✅ Joining any train
✅ Viewing train schedules
✅ Getting notifications
✅ Message forwarding

🎫 **What Needs Subscription ($5/month):**
🔒 Creating new trains
🔒 Managing schedules
🔒 Analytics & stats

**Who needs to subscribe?**
Only people who want to CREATE/MANAGE trains. If you just want to join trains, you don't need anything!

**Questions?** Use `/subscriptionstatus` to check your status or `/subscribe` to get started!
```

---

## 🛠️ **Commands Reference**

### User Commands:
- `/subscriptionstatus` - Check if subscriptions are required & your status
- `/subscribe` - Get subscription link
- `/managesubscription` - View/manage your subscription

### Owner Commands:
- `/togglesubscriptions <enable/disable>` - Turn system on/off
- `/substats` - View subscription statistics

---

**Note:** This system is currently DISABLED and won't be enforced until you activate it. You can test all commands without subscriptions right now!
