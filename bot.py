import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import asyncio
from datetime import datetime
import pytz
import os
import base64
import io

# ============================================================
#  CONFIG
# ============================================================
BOT_TOKEN          = os.environ["CHART_BOT_TOKEN"]
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
CHART_IMG_API_KEY  = os.environ["CHART_IMG_API_KEY"]
SYMBOL             = "OANDA:XAUUSD"
TZ_THAI            = pytz.timezone("Asia/Bangkok")
CHART_CHANNEL_NAME = "ccpro-ai-strategy" # ← ส่ง morning chart เข้าช่องนี้
MORNING_HOUR       = 8
MORNING_MINUTE     = 0

TIMEFRAMES = {
    "H1":  "1h",
    "M30": "30m",
    "M15": "15m",
    "M5":  "5m",
}

# ============================================================
#  CHANNEL FINDER — รองรับ emoji นำหน้าชื่อช่อง เช่น 🍎ccpro-ai-strategy
# ============================================================
def find_channel(guild: discord.Guild, name: str) -> discord.TextChannel | None:
    for ch in guild.text_channels:
        if ch.name == name: return ch
    for ch in guild.text_channels:
        if ch.name.endswith(name): return ch
    return None

# ============================================================
#  DISCORD SETUP
# ============================================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ============================================================
#  ดึงกราฟจาก chart-img.com
# ============================================================
async def fetch_chart(timeframe: str) -> bytes | None:
    interval = TIMEFRAMES.get(timeframe, "1h")
    headers  = {
        "x-api-key": CHART_IMG_API_KEY,
        "content-type": "application/json"
    }
    payload = {
        "symbol":   SYMBOL,
        "interval": interval,
        "theme":    "dark",
        "width":    800,
        "height":   600,
        "studies": [
            {"name": "Moving Average", "input": {"length": 50}},
            {"name": "Moving Average", "input": {"length": 200}},
            {"name": "Stochastic"}
        ]
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.chart-img.com/v2/tradingview/advanced-chart",
                headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                if r.status == 200:
                    return await r.read()
                else:
                    print(f"Chart error ({timeframe}): {r.status} {await r.text()}")
    except Exception as e:
        print(f"Chart fetch error ({timeframe}): {e}")
    return None

async def fetch_all_charts() -> dict:
    """ดึงทุก timeframe พร้อมกัน คืน dict {tf: bytes}"""
    results = {}
    for tf in ["H1", "M30", "M15", "M5"]:
        results[tf] = await fetch_chart(tf)
    return {tf: img for tf, img in results.items() if img}

# ============================================================
#  AI วิเคราะห์กราฟด้วย Claude Vision
# ============================================================
async def ai_analyze_chart(images: dict) -> str:
    content = []
    for tf in ["H1", "M30", "M15", "M5"]:
        if tf in images:
            img_b64 = base64.standard_b64encode(images[tf]).decode("utf-8")
            content.append({"type": "text", "text": f"--- กราฟ {tf} ---"})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": img_b64}
            })

    content.append({
        "type": "text",
        "text": (
            "คุณเป็น Price Action trader เชี่ยวชาญ XAUUSD\n"
            "วิเคราะห์กราฟทั้ง 4 Timeframe (H1, M30, M15, M5) ที่แนบมา\n"
            "โดยใช้ EMA 20/50/200, Stochastic และ Price Action\n\n"
            "กรุณาวิเคราะห์เป็นภาษาไทย ในรูปแบบนี้:\n\n"
            "📊 **ภาพรวมตลาด**\n"
            "- Trend หลัก (H1): ...\n"
            "- โครงสร้างราคา: ...\n\n"
            "🎯 **จุดเข้า (Entry)**\n"
            "- Buy Zone: ...\n"
            "- Sell Zone: ...\n\n"
            "✅ **เป้าหมาย (TP)**\n"
            "- TP1: ...\n"
            "- TP2: ...\n\n"
            "🛡️ **จุดตัดขาดทุน (SL)**\n"
            "- SL Buy: ...\n"
            "- SL Sell: ...\n\n"
            "⚠️ **ข้อควรระวัง**\n"
            "- ...\n\n"
            "หมายเหตุ: นี่คือการวิเคราะห์เพื่อประกอบการตัดสินใจเท่านั้น ไม่ใช่คำแนะนำการลงทุน"
        )
    })

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": "claude-sonnet-4-6",   # ← อัปเดตแล้ว
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": content}]
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers, json=body,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as r:
                data = await r.json(content_type=None)
                if "content" in data and len(data["content"]) > 0:
                    return data["content"][0].get("text", "ไม่มีข้อมูลครับ")
                elif "error" in data:
                    return f"❌ API Error: {data['error'].get('message', 'unknown')}"
    except Exception as e:
        return f"❌ เชื่อมต่อไม่ได้: {e}"
    return "❌ ไม่สามารถวิเคราะห์ได้ครับ"

# ============================================================
#  HELPER — ส่งกราฟ + วิเคราะห์ไปยัง channel
# ============================================================
async def send_chart_analysis(channel: discord.TextChannel, label: str = ""):
    """ดึงกราฟ + AI วิเคราะห์ แล้วส่งเข้า channel"""
    now   = datetime.now(TZ_THAI)
    images = await fetch_all_charts()

    if not images:
        await channel.send("❌ ดึงกราฟไม่ได้ครับ กรุณาลองใหม่ภายหลัง")
        return

    # ส่งกราฟทั้งหมด
    files = [
        discord.File(fp=io.BytesIO(images[tf]), filename=f"XAUUSD_{tf}.png")
        for tf in ["H1", "M30", "M15", "M5"] if tf in images
    ]
    header = label or f"📈 **XAUUSD Chart Analysis** — {now.strftime('%d/%m/%Y %H:%M')} น."
    await channel.send(content=header, files=files)

    # AI วิเคราะห์
    analysis = await ai_analyze_chart(images)
    embed = discord.Embed(
        title="🤖 AI วิเคราะห์ XAUUSD — Multi-Timeframe",
        description=analysis,
        color=discord.Color.purple(),
        timestamp=now
    )
    embed.set_footer(
        text=f"XAU Chart Bot • {now.strftime('%d/%m/%Y %H:%M')} น. • powered by Claude AI"
    )
    await channel.send(embed=embed)

# ============================================================
#  AUTO MORNING CHART BRIEFING — 08:00 UTC+7 จ-ศ
# ============================================================
@tasks.loop(minutes=1)
async def morning_chart_briefing():
    now = datetime.now(TZ_THAI)
    if now.weekday() > 4: return                              # ข้ามเสาร์-อาทิตย์
    if now.hour != MORNING_HOUR or now.minute != MORNING_MINUTE: return   # เฉพาะ 08:00

    try:
        guild = discord.utils.get(bot.guilds)
        if not guild: return

        channel = find_channel(guild, CHART_CHANNEL_NAME)
        if not channel:
            print(f"⚠️ morning_chart_briefing: ไม่เจอช่อง #{CHART_CHANNEL_NAME} (ข้ามรอบนี้)")
            return

        print(f"🌅 Morning chart briefing: {now.strftime('%d/%m/%Y %H:%M')}")
        label = f"🌅 **Morning Chart Briefing — {now.strftime('%A %d/%m/%Y')}**\nEMA 50/200 + Stochastic | H1 · M30 · M15 · M5"
        await send_chart_analysis(channel, label)
        print("✅ Morning chart briefing ส่งแล้ว")
    except Exception as e:
        print(f"❌ Morning chart briefing error (ไม่กระทบ task อื่น): {e}")
        try:
            guild   = discord.utils.get(bot.guilds)
            channel = find_channel(guild, CHART_CHANNEL_NAME) if guild else None
            if channel:
                await channel.send(f"⚠️ Morning chart briefing เกิดข้อผิดพลาด: {e}")
        except Exception:
            pass

# ============================================================
#  BUTTONS VIEW
# ============================================================
class ChartView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="📊 วิเคราะห์ทั้ง 4 Timeframe",
                       style=discord.ButtonStyle.primary, row=0)
    async def analyze_all(self, interaction: discord.Interaction,
                          button: discord.ui.Button):
        await interaction.response.defer(thinking=True)
        await self._fetch_and_analyze(interaction, ["H1", "M30", "M15", "M5"])

    @discord.ui.button(label="H1",  style=discord.ButtonStyle.secondary, row=1)
    async def chart_h1(self, interaction, button):
        await interaction.response.defer(thinking=True)
        await self._fetch_single(interaction, "H1")

    @discord.ui.button(label="M30", style=discord.ButtonStyle.secondary, row=1)
    async def chart_m30(self, interaction, button):
        await interaction.response.defer(thinking=True)
        await self._fetch_single(interaction, "M30")

    @discord.ui.button(label="M15", style=discord.ButtonStyle.secondary, row=1)
    async def chart_m15(self, interaction, button):
        await interaction.response.defer(thinking=True)
        await self._fetch_single(interaction, "M15")

    @discord.ui.button(label="M5",  style=discord.ButtonStyle.secondary, row=1)
    async def chart_m5(self, interaction, button):
        await interaction.response.defer(thinking=True)
        await self._fetch_single(interaction, "M5")

    async def _fetch_single(self, interaction, tf):
        img = await fetch_chart(tf)
        if not img:
            await interaction.followup.send(f"❌ ดึงกราฟ {tf} ไม่ได้ครับ", ephemeral=True)
            return
        file  = discord.File(fp=io.BytesIO(img), filename=f"XAUUSD_{tf}.png")
        embed = discord.Embed(
            title=f"📈 XAUUSD — {tf}",
            color=discord.Color.gold(),
            timestamp=datetime.now(TZ_THAI)
        )
        embed.set_image(url=f"attachment://XAUUSD_{tf}.png")
        embed.set_footer(text="XAU Chart Bot • EMA 50/200 + Stochastic")
        await interaction.followup.send(file=file, embed=embed)

    async def _fetch_and_analyze(self, interaction, timeframes):
        images = await fetch_all_charts()
        if not images:
            await interaction.followup.send("❌ ดึงกราฟไม่ได้เลยครับ ลองใหม่อีกครั้ง")
            return
        files = [
            discord.File(fp=io.BytesIO(images[tf]), filename=f"XAUUSD_{tf}.png")
            for tf in ["H1", "M30", "M15", "M5"] if tf in images
        ]
        await interaction.followup.send(
            content="⏳ กำลังวิเคราะห์กราฟทั้งหมด รอแป๊บนึงนะครับ...",
            files=files
        )
        analysis = await ai_analyze_chart(images)
        now   = datetime.now(TZ_THAI)
        embed = discord.Embed(
            title="🤖 AI วิเคราะห์ XAUUSD — Multi-Timeframe",
            description=analysis,
            color=discord.Color.purple(),
            timestamp=now
        )
        embed.set_footer(
            text=f"XAU Chart Bot • {now.strftime('%d/%m/%Y %H:%M')} น. • powered by Claude AI"
        )
        await interaction.followup.send(embed=embed)

# ============================================================
#  SLASH COMMAND
# ============================================================
@tree.command(name="chart", description="ดึงกราฟ XAUUSD พร้อมปุ่มวิเคราะห์")
async def cmd_chart(interaction: discord.Interaction):
    now   = datetime.now(TZ_THAI)
    embed = discord.Embed(
        title="📈 XAUUSD Chart Analysis",
        description=(
            "กดปุ่มด้านล่างเพื่อดูกราฟหรือวิเคราะห์ครับ\n\n"
            "**📊 วิเคราะห์ทั้ง 4 Timeframe** — ดึงกราฟ + AI วิเคราะห์ครบ\n"
            "**H1 / M30 / M15 / M5** — ดูกราฟ Timeframe เดียว"
        ),
        color=discord.Color.gold(),
        timestamp=now
    )
    embed.add_field(name="Symbol",     value="`XAUUSD`",              inline=True)
    embed.add_field(name="Indicators", value="EMA 50/200 + Stochastic", inline=True)
    embed.set_footer(text="XAU Chart Bot • เวลาไทย (UTC+7)")
    await interaction.response.send_message(embed=embed, view=ChartView())

# ============================================================
#  BOT EVENTS
# ============================================================
@bot.event
async def on_ready():
    print(f"✅ {bot.user} พร้อมใช้งานแล้วครับ!")
    await tree.sync()
    print("✅ Slash commands synced!")
    morning_chart_briefing.start()
    now = datetime.now(TZ_THAI)
    # หาเวลา 08:00 ถัดไป
    next_8 = now.replace(hour=MORNING_HOUR, minute=MORNING_MINUTE, second=0, microsecond=0)
    if now >= next_8:
        from datetime import timedelta
        next_8 += timedelta(days=1)
    diff = (next_8 - now).total_seconds() / 3600
    print(f"🌅 Morning chart briefing จะยิงใน {round(diff, 1)} ชม. ({next_8.strftime('%d/%m %H:%M')} UTC+7)")

    # ── Catch-up: ถ้า bot restart หลัง 08:00-08:30 ของวันนี้ (วันธรรมดา) ──
    # จำกัดช่วงเวลาแคบ (08:00-08:30) เพื่อลดความเสี่ยงส่งซ้ำถ้า restart ตอนสายมาก
    if now.weekday() <= 4 and now.hour == MORNING_HOUR and MORNING_MINUTE <= now.minute <= MORNING_MINUTE + 30:
        print("🔄 ตรวจพบว่า bot restart ใกล้ช่วง 08:00 — ส่ง chart briefing ที่อาจพลาดไป")
        try:
            guild = discord.utils.get(bot.guilds)
            if guild:
                channel = find_channel(guild, CHART_CHANNEL_NAME)
                if channel:
                    label = (f"🌅 **Morning Chart Briefing — {now.strftime('%A %d/%m/%Y')}** "
                              f"_(ส่งย้อนหลังเนื่องจาก bot เพิ่ง restart)_\n"
                              f"EMA 50/200 + Stochastic | H1 · M30 · M15 · M5")
                    await send_chart_analysis(channel, label)
                    print("✅ catch-up chart briefing ส่งแล้ว")
        except Exception as e:
            print(f"❌ catch-up chart briefing error: {e}")

bot.run(BOT_TOKEN)
