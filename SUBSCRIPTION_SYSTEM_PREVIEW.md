# 🎫 Train Bot Subscription System - Complete Preview

## 📋 System Overview

The subscription system is **fully built and ready** but currently **DISABLED**. When activated, it manages access to organizer features while keeping the bot free for participants.

---

## 🎯 Core Features Built

### 1️⃣ **Automatic 14-Day Free Trial**
- ✅ No credit card required
- ✅ Auto-starts when user first uses an organizer command
- ✅ Tracks which server they're in
- ✅ Shows countdown of days remaining
- ✅ Server owners get notified 7 days before member trials expire

### 2️⃣ **Access Hierarchy** (In Order of Priority)
1. **Bot Owner** → Unlimited access, no subscription
2. **Grace Period** → Everyone has FREE access until January 1, 2026
3. **Trusted Roles** → Server-specific FREE access (manually activated by owner after grace period)
4. **Free Trial** → 14 days of full access for new users
5. **Active Subscribers** → $12/month for access across ALL servers
6. **Everyone Else** → Can join trains and use basic features for FREE

### 3️⃣ **Pricing Structure**
- **Free Trial:** 14 days, no credit card
- **Subscription:** $12/month per organizer
- **Scope:** Global - works in ALL servers
- **Rationale:** Bot costs $40/month to host 24/7

### 4️⃣ **Grace Period System**
- 30 days of FREE access for everyone when first enabled
- Currently set to end **January 1, 2026**
- Gives users time to try features before deciding
- Countdown shows days remaining

---

## 🔧 Available Commands

### For All Users:
- **`/subscriptioninfo`** - View subscription system info and personal status
  - Shows if system is active/disabled
  - Shows your trial status (days remaining, expired, eligible)
  - Shows subscription status
  - Shows grace period countdown
  - Shows pricing and feature breakdown
  
- **`/subscriptionstatus`** - Quick status check
  - System enabled/disabled
  - Your subscription status
  - Trial information

### For Bot Owner Only:
- **`/togglesubscriptions enable`** - Activate the subscription system
  - Starts 30-day grace period
  - Sends announcements to all servers
  - Optional: Set Stripe Price ID
  
- **`/togglesubscriptions disable`** - Deactivate the subscription system
  - Returns all features to FREE
  - Preserves subscription data

- **`/substats`** - View subscription statistics
  - Total subscribers
  - Active vs inactive
  - Grace period info

### For Server Admins:
- **`/addtrustedrole @Role`** - Grant FREE organizer access to a role
- **`/removetrustedrole @Role`** - Remove trusted role access
- **`/listtrustedroles`** - View all trusted roles in server
- **`/enabletrustedaccess`** - [Owner] Manually activate trusted access for a server
- **`/disabletrustedaccess`** - [Owner] Disable trusted access for a server

### Planned (Not Yet Active):
- **`/subscribe`** - Generate Stripe payment link
- **`/managesubscription`** - Stripe customer portal integration

---

## 💰 Free vs Paid Features

### ✅ **ALWAYS FREE** (No Subscription Needed)
- Joining trains
- Leaving trains  
- Viewing schedules & rosters
- Getting notifications
- Message forwarding (all users)
- Basic commands (/ping, /help, /info, etc.)

### 🔒 **REQUIRES SUBSCRIPTION** (When Active)
- Creating train schedules
- Managing trains
- Google Sheets integration
- Persistent displays
- Analytics & statistics
- Twitch integration
- Live role management

---

## 🎁 Trial System Features

### Auto-Start Mechanism:
When subscriptions are enabled, users automatically start their 14-day trial when they:
- Create a train schedule
- Set up timeslots
- Use Google Sheets sync
- Access any organizer feature

### Trial Tracking:
- **Start Date** - Recorded in database
- **End Date** - Calculated as start + 14 days
- **Server** - Tracks which server they activated trial in
- **Status** - Active, Expired, or Eligible
- **Days Remaining** - Uses ceiling logic (shows "1 day" even with hours left)

### Trial Expiry Notifications:
- **When:** 7 days before a trial expires
- **Who:** Server owner receives DM
- **Contains:**
  - Member name and trial end date
  - What happens when trial ends
  - Subscription pricing ($12/month)
  - **Alternative:** How to grant FREE trusted access
  - Link to check trial status

---

## 📊 What Users See

### When System is **DISABLED** (Current State):

```
🚂 Train Bot Subscription System
📢 CURRENTLY NOT ACTIVE - PREVIEW ONLY

This subscription system is built and ready but not yet enabled.
All features are currently FREE for everyone while we prepare for launch.

🔒 System Status
NOT ACTIVE - Everything is free during preview period

🎯 How It Will Work
When activated, train organizers will need subscriptions to create/manage trains.
Participants who just want to join trains will remain FREE forever!

👥 Who Has Access?
1️⃣ Bot Owner - Unlimited access (no subscription)
2️⃣ Trusted Roles - Free access in their designated servers
3️⃣ Free Trial - 14 days full access for new users
4️⃣ Subscribers - Access in ALL servers ($12/month)
5️⃣ Everyone Else - Can join trains for FREE

💰 Always FREE
✅ Joining trains
✅ Leaving trains
✅ Viewing schedules & rosters
✅ Getting notifications
✅ Message forwarding
✅ Basic commands

🎫 Subscription Features
🔒 Creating train schedules
🔒 Managing trains
🔒 Google Sheets integration
🔒 Persistent displays
🔒 Analytics & stats
🔒 Twitch integration

💵 Pricing (When Active)
🎁 14-Day FREE Trial - No credit card required
$12/month per user after trial
• Works across ALL servers
• Cancel anytime
• Full access to premium features
• Trial starts automatically on first organizer action

❓ Why Subscriptions?
This bot costs $40/month to host 24/7. Subscriptions help cover these costs
while keeping the bot free for participants. Only train organizers pay -
everyone else enjoys free access!

📅 Timeline
Current: Preview period - all features FREE
When Activated: Grace period begins (ends January 1, 2026)
During Grace Period: Everyone keeps FREE access
After Jan 1, 2026: 14-day free trial, then subscription required

⚠️ NOT ACTIVE YET - All features currently FREE
```

### When System is **ENABLED** (After `/togglesubscriptions enable`):

**Example: User with Active Trial**
```
🚂 Train Bot Subscription System
Subscription system is now ACTIVE
Check your access status below.

🎁 Your Status
Free Trial Active
• 10 days remaining
• Trial ends: November 15, 2025
• Full access to all features

🎉 Grace Period Active
Everyone has FREE access for 45 more days
Grace period ends: January 01, 2026

[Same features breakdown as above...]
```

**Example: User Eligible for Trial**
```
🎁 Your Status
Trial Available!
Start your 14-day FREE trial automatically by using any organizer command
```

**Example: User with Expired Trial**
```
⏰ Your Status
Trial Expired - Subscribe to continue using organizer features
```

**Example: Active Subscriber**
```
✅ Your Status
Active Subscriber - Full access to all features
```

---

## 🛡️ Server Owner Benefits

### Trusted Roles Feature:
Server owners can designate roles that get **FREE organizer access** in their server:

1. **Use Case:** Give your moderators/staff free access
2. **Command:** `/addtrustedrole @Staff`
3. **Benefit:** No subscription needed for trusted users
4. **Limitation:** Only works in that specific server
5. **Activation:** Automatically works during grace period; requires manual activation by bot owner after January 1, 2026

### Trial Expiry Notifications:
When a server member's trial is expiring in 7 days, the server owner receives:
- DM notification with member details
- Trial end date and days remaining
- Explanation of what happens next
- Subscription pricing information
- **Alternative option:** How to grant them FREE trusted access

---

## 🔄 Activation Process

### When You Run `/togglesubscriptions enable`:

1. **System Activates**
   - Subscription requirement flag set to TRUE
   - Grace period starts (30 days)
   - Grace end date set to January 1, 2026

2. **Announcements Sent**
   - Bot sends embed to all servers it's in
   - Contains grace period info
   - Shows timeline and pricing
   - Explains what's changing

3. **Database Setup**
   - Creates subscription_settings record
   - Sets pricing to $12/month (1200 cents)
   - Tracks who enabled it and when

4. **Users Start Trials**
   - First organizer command auto-starts trial
   - 14-day countdown begins
   - Notification sent 7 days before expiry

---

## 📈 Database Schema

### Tables Created:
1. **`subscription_settings`**
   - subscription_required (boolean)
   - subscription_price_monthly (integer, cents)
   - free_trial_days (integer, default 14)
   - stripe_price_id (string)
   - grace_period_days (integer)
   - grace_period_end_date (date)
   - updated_by (bigint, user ID)
   - enabled_at (timestamp)

2. **`user_subscriptions`**
   - user_id (bigint)
   - username (string)
   - stripe_customer_id (string)
   - stripe_subscription_id (string)
   - stripe_status (string)
   - is_active (boolean)
   - subscription_start (timestamp)
   - subscription_end (timestamp)
   - current_period_start (timestamp)
   - current_period_end (timestamp)
   - **is_trial** (boolean)
   - **trial_start** (timestamp)
   - **trial_end** (timestamp)
   - **trial_expiry_notification_sent** (boolean)
   - **trial_first_used_guild_id** (bigint)

3. **`trusted_server_access`**
   - guild_id (bigint)
   - enabled (boolean)
   - enabled_by (bigint)
   - enabled_at (timestamp)

---

## ⚙️ Background Tasks

### 1. Trial Expiry Checker
- **Runs:** Every 24 hours
- **Purpose:** Find trials expiring in 7 days
- **Action:** Send DM to server owners
- **Status:** ✅ Active and running
- **Log:** "📊 Found 0 trials expiring in ~7 days" (currently no trials)

### 2. Subscription Sync (Planned)
- Sync with Stripe for subscription status
- Update payment statuses
- Handle failed payments

---

## 💳 Stripe Integration Status

### ✅ **Built and Ready:**
- Database schema for Stripe IDs
- Subscription tracking
- Trial system
- Access control
- Webhook structure

### 🚧 **Not Yet Active:**
- Actual Stripe payment processing
- Customer Portal links
- `/subscribe` command functionality
- `/managesubscription` portal integration
- Webhook handlers for payment events

---

## 🎯 Testing the System

### To See It In Action:

1. **Activate System** (Owner only):
   ```
   /togglesubscriptions enable
   ```

2. **Check Your Status**:
   ```
   /subscriptioninfo
   ```

3. **Try an Organizer Command**:
   ```
   /addslot
   ```
   - If subscriptions are enabled, your trial auto-starts
   - System shows trial countdown

4. **Check Stats** (Owner only):
   ```
   /substats
   ```

5. **Set Up Trusted Roles**:
   ```
   /addtrustedrole @Moderator
   ```

### To Disable:
```
/togglesubscriptions disable
```

---

## 📝 Key Design Decisions

1. **Message Forwarding = Always Free**
   - Encourages bot adoption
   - Low-cost feature to host
   - Builds user base

2. **Global Subscriptions**
   - One subscription works everywhere
   - Easier for users
   - More valuable than per-server

3. **14-Day Trial, No Credit Card**
   - Reduces friction
   - Builds trust
   - Higher conversion rates

4. **Grace Period Until Jan 1, 2026**
   - Gives users time to adapt
   - Shows goodwill
   - Prevents surprise lockouts

5. **Server Owner Notifications**
   - Helps retention
   - Shows transparency
   - Provides alternatives (trusted roles)

6. **Trusted Roles System**
   - Gives server owners control
   - Allows free team access
   - Encourages bot adoption

---

## 🚀 Ready to Launch

The subscription system is **production-ready** and waiting for:
1. Stripe account setup
2. Price ID from Stripe dashboard
3. Decision to activate via `/togglesubscriptions enable`

All core functionality is built, tested, and operational! 🎉
