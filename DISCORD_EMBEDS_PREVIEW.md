# 🎨 Discord Embed Messages - Visual Preview

This shows exactly what users see when they run subscription-related commands in Discord.

---

## 1️⃣ `/subscriptioninfo` - SYSTEM DISABLED (Current State)

```
╔═══════════════════════════════════════════════════════════════╗
║  🚂 Train Bot Subscription System                             ║
║  📢 CURRENTLY NOT ACTIVE - PREVIEW ONLY                       ║
║                                                                ║
║  This subscription system is built and ready but not yet      ║
║  enabled. All features are currently FREE for everyone        ║
║  while we prepare for launch.                                 ║
╠═══════════════════════════════════════════════════════════════╣
║                                                                ║
║  🔒 System Status                                             ║
║  ──────────────────────────────────────────────────────────   ║
║  NOT ACTIVE - Everything is free during preview period        ║
║                                                                ║
║  🎯 How It Will Work                                          ║
║  ──────────────────────────────────────────────────────────   ║
║  When activated, train organizers will need subscriptions     ║
║  to create/manage trains.                                     ║
║  Participants who just want to join trains will remain        ║
║  FREE forever!                                                 ║
║                                                                ║
║  👥 Who Has Access?                                           ║
║  ──────────────────────────────────────────────────────────   ║
║  1️⃣ Bot Owner - Unlimited access (no subscription)           ║
║  2️⃣ Trusted Roles - Free access in their designated servers  ║
║  3️⃣ Free Trial - 14 days full access for new users           ║
║  4️⃣ Subscribers - Access in ALL servers ($12/month)          ║
║  5️⃣ Everyone Else - Can join trains for FREE                 ║
║                                                                ║
║  💰 Always FREE          🎫 Subscription Features             ║
║  ─────────────────       ───────────────────────────          ║
║  ✅ Joining trains       🔒 Creating train schedules          ║
║  ✅ Leaving trains       🔒 Managing trains                   ║
║  ✅ Viewing schedules    🔒 Google Sheets integration         ║
║  ✅ Getting notifications 🔒 Persistent displays              ║
║  ✅ Message forwarding   🔒 Analytics & stats                 ║
║  ✅ Basic commands       🔒 Twitch integration                ║
║                                                                ║
║  💵 Pricing (When Active)                                     ║
║  ──────────────────────────────────────────────────────────   ║
║  🎁 14-Day FREE Trial - No credit card required               ║
║  $12/month per user after trial                               ║
║  • Works across ALL servers                                   ║
║  • Cancel anytime                                             ║
║  • Full access to premium features                            ║
║  • Trial starts automatically on first organizer action       ║
║                                                                ║
║  ❓ Why Subscriptions?                                        ║
║  ──────────────────────────────────────────────────────────   ║
║  This bot costs $40/month to host 24/7. Subscriptions help    ║
║  cover these costs while keeping the bot free for             ║
║  participants. Only train organizers pay - everyone else      ║
║  enjoys free access!                                          ║
║                                                                ║
║  🛡️ For Server Administrators                                ║
║  ──────────────────────────────────────────────────────────   ║
║  You can designate trusted roles who get free organizer       ║
║  access in your server only.                                  ║
║                                                                ║
║  Use /addtrustedrole @RoleName to grant free access to        ║
║  your staff/moderators.                                       ║
║  Use /listtrustedroles to view current trusted roles.         ║
║                                                                ║
║  📅 Timeline                                                  ║
║  ──────────────────────────────────────────────────────────   ║
║  Current: Preview period - all features FREE                  ║
║  When Activated: Grace period begins (ends January 1, 2026)   ║
║  During Grace Period: Everyone keeps FREE access              ║
║  After Jan 1, 2026: 14-day free trial, then subscription      ║
║                     required                                  ║
║                                                                ║
║  ⚠️ NOT ACTIVE YET - All features currently FREE             ║
║                                                                ║
║  Timestamp: October 29, 2025 at 2:00 PM                       ║
╚═══════════════════════════════════════════════════════════════╝
```

**Color:** 🟠 Orange (#ffa500)  
**Visibility:** Public (not ephemeral)

---

## 2️⃣ `/subscriptioninfo` - SYSTEM ENABLED (After Activation)

### Example A: User on Active Free Trial

```
╔═══════════════════════════════════════════════════════════════╗
║  🚂 Train Bot Subscription System                             ║
║  Subscription system is now ACTIVE                            ║
║  Check your access status below.                              ║
╠═══════════════════════════════════════════════════════════════╣
║                                                                ║
║  🎁 Your Status                                               ║
║  ──────────────────────────────────────────────────────────   ║
║  Free Trial Active                                            ║
║  • 10 days remaining                                          ║
║  • Trial ends: November 15, 2025                              ║
║  • Full access to all features                                ║
║                                                                ║
║  🎉 Grace Period Active                                       ║
║  ──────────────────────────────────────────────────────────   ║
║  Everyone has FREE access for 45 more days                    ║
║  Grace period ends: January 01, 2026                          ║
║                                                                ║
║  [Same features breakdown as above...]                        ║
║                                                                ║
╚═══════════════════════════════════════════════════════════════╝
```

**Color:** 🟢 Green (#00ff00)  
**Shows:** Days remaining in trial + grace period countdown

---

### Example B: User Eligible for Trial (Never Used)

```
╔═══════════════════════════════════════════════════════════════╗
║  🚂 Train Bot Subscription System                             ║
║  Subscription system is now ACTIVE                            ║
║  Check your access status below.                              ║
╠═══════════════════════════════════════════════════════════════╣
║                                                                ║
║  🎁 Your Status                                               ║
║  ──────────────────────────────────────────────────────────   ║
║  Trial Available!                                             ║
║  Start your 14-day FREE trial automatically by using any      ║
║  organizer command                                            ║
║                                                                ║
║  [Features and pricing info...]                               ║
║                                                                ║
╚═══════════════════════════════════════════════════════════════╝
```

**Color:** 🟢 Green (#00ff00)

---

### Example C: Expired Trial (No Subscription)

```
╔═══════════════════════════════════════════════════════════════╗
║  🚂 Train Bot Subscription System                             ║
║  Subscription system is now ACTIVE                            ║
║  Check your access status below.                              ║
╠═══════════════════════════════════════════════════════════════╣
║                                                                ║
║  ⏰ Your Status                                               ║
║  ──────────────────────────────────────────────────────────   ║
║  Trial Expired - Subscribe to continue using organizer        ║
║  features                                                     ║
║                                                                ║
║  [Subscribe button/info would appear here...]                 ║
║                                                                ║
╚═══════════════════════════════════════════════════════════════╝
```

**Color:** 🟢 Green (#00ff00)

---

### Example D: Active Subscriber

```
╔═══════════════════════════════════════════════════════════════╗
║  🚂 Train Bot Subscription System                             ║
║  Subscription system is now ACTIVE                            ║
║  Check your access status below.                              ║
╠═══════════════════════════════════════════════════════════════╣
║                                                                ║
║  ✅ Your Status                                               ║
║  ──────────────────────────────────────────────────────────   ║
║  Active Subscriber - Full access to all features              ║
║                                                                ║
║  [Features info...]                                           ║
║                                                                ║
╚═══════════════════════════════════════════════════════════════╝
```

**Color:** 🟢 Green (#00ff00)

---

### Example E: Bot Owner

```
╔═══════════════════════════════════════════════════════════════╗
║  🚂 Train Bot Subscription System                             ║
║  Subscription system is now ACTIVE                            ║
║  Check your access status below.                              ║
╠═══════════════════════════════════════════════════════════════╣
║                                                                ║
║  ✅ Your Status                                               ║
║  ──────────────────────────────────────────────────────────   ║
║  Bot Owner - Unlimited access to all features                 ║
║                                                                ║
║  [Full system info for owner...]                              ║
║                                                                ║
╚═══════════════════════════════════════════════════════════════╝
```

**Color:** 🟢 Green (#00ff00)

---

## 3️⃣ Grace Period Announcement (Sent When System is Enabled)

This embed is automatically sent to **all servers** when you run `/togglesubscriptions enable`:

```
╔═══════════════════════════════════════════════════════════════╗
║  🎉 Important Announcement: Subscription System               ║
╠═══════════════════════════════════════════════════════════════╣
║                                                                ║
║  🚂 Train Bot Subscription System is Now Active!              ║
║                                                                ║
║  🎁 30-Day Grace Period Active                                ║
║  ──────────────────────────────────────────────────────────   ║
║  Don't worry! You still have 30 days of FREE access to all    ║
║  features. Nothing changes for you today.                     ║
║                                                                ║
║  Grace period ends: January 01, 2026                          ║
║                                                                ║
║  🎯 What's Changing?                                          ║
║  ──────────────────────────────────────────────────────────   ║
║  After the grace period, train organizers will need a         ║
║  subscription to create and manage trains.                    ║
║                                                                ║
║  Participants (people who join trains) will always be FREE!   ║
║                                                                ║
║  🎁 14-Day Free Trial                                         ║
║  ──────────────────────────────────────────────────────────   ║
║  • Starts automatically when you use organizer features       ║
║  • No credit card required                                    ║
║  • Full access to everything                                  ║
║  • Cancel anytime                                             ║
║                                                                ║
║  💰 Subscription Details                                      ║
║  ──────────────────────────────────────────────────────────   ║
║  Price: $12/month per organizer                               ║
║  Scope: Works across ALL servers                              ║
║  Benefits: Full access to all features                        ║
║                                                                ║
║  💰 Always FREE                                               ║
║  ──────────────────────────────────────────────────────────   ║
║  ✅ Joining trains                                            ║
║  ✅ Leaving trains                                            ║
║  ✅ Viewing schedules                                         ║
║  ✅ Message forwarding                                        ║
║  ✅ Basic commands                                            ║
║                                                                ║
║  🎫 Subscription Features                                     ║
║  ──────────────────────────────────────────────────────────   ║
║  🔒 Creating schedules                                        ║
║  🔒 Managing trains                                           ║
║  🔒 Google Sheets sync                                        ║
║  🔒 Analytics & stats                                         ║
║  🔒 Twitch integration                                        ║
║                                                                ║
║  🛡️ Server Administrators                                    ║
║  ──────────────────────────────────────────────────────────   ║
║  You can grant FREE organizer access to your staff:           ║
║  Use /addtrustedrole @RoleName                                ║
║                                                                ║
║  ℹ️ Questions?                                                ║
║  ──────────────────────────────────────────────────────────   ║
║  Use /subscriptioninfo to check your status anytime           ║
║                                                                ║
╚═══════════════════════════════════════════════════════════════╝
```

**Color:** 🟠 Orange (#ffa500)  
**Sent to:** All servers where bot is active  
**When:** Immediately after `/togglesubscriptions enable`

---

## 4️⃣ Trial Expiry Notification (DM to Server Owner)

This is sent to server owners **7 days before** a member's trial expires:

```
╔═══════════════════════════════════════════════════════════════╗
║  ⏰ Trial Ending Soon - Server Member                         ║
╠═══════════════════════════════════════════════════════════════╣
║                                                                ║
║  One of your server members' free trial is ending soon!       ║
║                                                                ║
║  Server: Game Lounge                                          ║
║  Member: @JohnDoe (ID: 123456789)                             ║
║  Trial Ends: November 15, 2025 at 02:30 PM UTC                ║
║  Days Remaining: ~7 days                                      ║
║                                                                ║
║  💰 What Happens Next?                                        ║
║  ──────────────────────────────────────────────────────────   ║
║  After the trial ends, this member will need a subscription   ║
║  to continue using organizer features like:                   ║
║  • Creating train schedules                                   ║
║  • Managing trains                                            ║
║  • Google Sheets integration                                  ║
║  • Analytics & statistics                                     ║
║                                                                ║
║  🎫 Subscription Details                                      ║
║  ──────────────────────────────────────────────────────────   ║
║  $12/month per organizer                                      ║
║  • Works across ALL servers globally                          ║
║  • Cancel anytime                                             ║
║  • Full access to premium features                            ║
║                                                                ║
║  🛡️ Free Alternative for Your Team                           ║
║  ──────────────────────────────────────────────────────────   ║
║  You can grant FREE organizer access to your trusted          ║
║  members:                                                     ║
║                                                                ║
║  Use /addtrustedrole @RoleName to give your staff free        ║
║  access                                                       ║
║  Use /addtrusteduser @Username for individual members         ║
║                                                                ║
║  Note: After the grace period (January 1, 2026), you'll       ║
║  need to enable trusted access for your server.               ║
║                                                                ║
║  ℹ️ More Information                                          ║
║  ──────────────────────────────────────────────────────────   ║
║  They can check their trial status with /subscriptioninfo     ║
║                                                                ║
║  Train Bot Subscription System                                ║
║  October 29, 2025 at 2:00 PM                                  ║
╚═══════════════════════════════════════════════════════════════╝
```

**Color:** 🟠 Orange (#ffa500)  
**Sent to:** Server owner (DM)  
**When:** 7 days before trial expiry  
**Frequency:** Once per trial (tracked via trial_expiry_notification_sent)

---

## 5️⃣ Access Denied Message (When Trial Expires)

When a user tries to use an organizer command after their trial expires:

```
╔═══════════════════════════════════════════════════════════════╗
║  🔒 Subscription Required                                     ║
╠═══════════════════════════════════════════════════════════════╣
║                                                                ║
║  This feature requires an active subscription.                ║
║                                                                ║
║  ⏰ Your 14-day free trial has expired                        ║
║                                                                ║
║  💡 How to Continue:                                          ║
║  ──────────────────────────────────────────────────────────   ║
║  1️⃣ Subscribe for $12/month (works in ALL servers)           ║
║  2️⃣ Ask your server admin to add you to a trusted role       ║
║                                                                ║
║  ℹ️ Use /subscriptioninfo for more details                   ║
║                                                                ║
╚═══════════════════════════════════════════════════════════════╝
```

**Color:** 🔴 Red (#ff0000)  
**Visibility:** Ephemeral (only user can see)

---

## 6️⃣ Trial Started Notification

When a user starts their trial (first organizer command):

```
╔═══════════════════════════════════════════════════════════════╗
║  🎁 Free Trial Started!                                       ║
╠═══════════════════════════════════════════════════════════════╣
║                                                                ║
║  Your 14-day FREE trial has begun!                            ║
║                                                                ║
║  ✅ Full access to all organizer features                     ║
║  ⏰ Trial ends: November 15, 2025                             ║
║  💳 No credit card required                                   ║
║                                                                ║
║  ℹ️ Check your status anytime with /subscriptioninfo         ║
║                                                                ║
╚═══════════════════════════════════════════════════════════════╝
```

**Color:** 🟢 Green (#00ff00)  
**Visibility:** Ephemeral (only user can see)  
**When:** First time using organizer command after subscriptions are enabled

---

## 🎨 Color Guide

- **🟢 Green (#00ff00):** Active/Success states (active trial, active subscription)
- **🟠 Orange (#ffa500):** Informational/Preview (system disabled, announcements, warnings)
- **🔴 Red (#ff0000):** Error/Denied (access denied, trial expired)
- **🔵 Blue (#5865F2):** Status checks (subscription status command)

---

## 📱 Testing in Discord

To see these embeds yourself:

1. **Current State (Disabled):**
   ```
   /subscriptioninfo
   ```
   → Shows orange "NOT ACTIVE" preview

2. **Activate System:**
   ```
   /togglesubscriptions enable
   ```
   → Sends announcements to all servers
   → System becomes active

3. **Check Your Status:**
   ```
   /subscriptioninfo
   ```
   → Shows green "ACTIVE" embed with your personal status

4. **Try an Organizer Command:**
   ```
   /addslot
   ```
   → If subscriptions enabled, starts your trial
   → Shows green "Trial Started" message

5. **Deactivate System:**
   ```
   /togglesubscriptions disable
   ```
   → Returns to preview mode

---

All embeds are professionally formatted, use clear icons, and provide actionable information to users!
