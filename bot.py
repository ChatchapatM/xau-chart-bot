import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
from datetime import datetime
import pytz
import os
import base64

# ============================================================
#  CONFIG
# ============================================================
BOT_TOKEN          = os.environ["CHART_BOT_TOKEN"]
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
CHART_IMG_API_KEY  = os.environ["CHART_IMG_API_KEY"]
SYMBOL             = "OANDA:XAUUSD"
TZ_THAI            = pytz.timezone("Asia/Bangkok")

TIMEFRAMES = {
    "H1":  "1h",
    "M30": "30m",
    "M15": "15m",
    "M5":  "5m",
}

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
    headers = {
        "x-api-key": CHART_IMG_API_KEY,
        "content-type": "application/json"
    }
    payload = {
        "symbol": SYMBOL,
        "interval": interval,
        "theme": "dark",
        "width": 800,
        "height": 600,
        "studies": [
            {"name": "Moving Average", "input": {"length": 20}},
            {"name": "Moving Average", "input": {"length": 50}},
            {"name": "Moving Average", "input": {"length": 200}},
            {"name": "Stochastic"}
        ]
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.chart-img.com/v2/tradingview/advanced-chart",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                if r.status == 200:
                    return await r.read()
                else:
                    text = await r.text()
                    print(f"Chart error ({timeframe}): {r.status} {text}")
    except Exception as e:
        print(f"Chart fetch error ({timeframe}): {e}")
    return None

# ============================================================
#  AI วิเคราะห์กราฟด้วย Claude Vision
# ============================================================
async def ai_analyze_chart(images: dict) -> str:
    content = []
    tf_order = ["H1", "M30", "M15", "M5"]
    for tf in tf_order:
        if tf in images:
            img_b64 = base64.standard_b64encode(images[tf]).decode("utf-8")
            content.append({
                "type": "text",
                "text": f"--- กราฟ {tf} ---"
            })
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": img_b64
                }
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
        "model": "claude-sonnet-4-5",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": content}]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers, json=body,
                timeout=aiohttp.ClientTimeout(total=40)
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

    @discord.ui.button(label="H1", style=discord.ButtonStyle.secondary, row=1)
    async def chart_h1(self, interaction: discord.Interaction,
                       button: discord.ui.Button):
        await interaction.response.defer(thinking=True)
        await self._fetch_single(interaction, "H1")

    @discord.ui.button(label="M30", style=discord.ButtonStyle.secondary, row=1)
    async def chart_m30(self, interaction: discord.Interaction,
                        button: discord.ui.Button):
        await interaction.response.defer(thinking=True)
        await self._fetch_single(interaction, "M30")

    @discord.ui.button(label="M15", style=discord.ButtonStyle.secondary, row=1)
    async def chart_m15(self, interaction: discord.Interaction,
                        button: discord.ui.Button):
        await interaction.response.defer(thinking=True)
        await self._fetch_single(interaction, "M15")

    @discord.ui.button(label="M5", style=discord.ButtonStyle.secondary, row=1)
    async def chart_m5(self, interaction: discord.Interaction,
                       button: discord.ui.Button):
        await interaction.response.defer(thinking=True)
        await self._fetch_single(interaction, "M5")

    async def _fetch_single(self, interaction, tf):
        img = await fetch_chart(tf)
        if not img:
            await interaction.followup.send(f"❌ ดึงกราฟ {tf} ไม่ได้ครับ", ephemeral=True)
            return
        file = discord.File(fp=__import__("io").BytesIO(img), filename=f"XAUUSD_{tf}.png")
        embed = discord.Embed(
            title=f"📈 XAUUSD — {tf}",
            color=discord.Color.gold(),
            timestamp=datetime.now(TZ_THAI)
        )
        embed.set_image(url=f"attachment://XAUUSD_{tf}.png")
        embed.set_footer(text="XAU Chart Bot • EMA 20/50/200 + Stochastic")
        await interaction.followup.send(file=file, embed=embed)

    async def _fetch_and_analyze(self, interaction, timeframes):
        tasks = {tf: fetch_chart(tf) for tf in timeframes}
        results = {}
        for tf, coro in tasks.items():
            results[tf] = await coro

        images = {tf: img for tf, img in results.items() if img}
        if not images:
            await interaction.followup.send("❌ ดึงกราฟไม่ได้เลยครับ ลองใหม่อีกครั้ง")
            return

        files = []
        for tf in ["H1", "M30", "M15", "M5"]:
            if tf in images:
                import io
                files.append(discord.File(
                    fp=io.BytesIO(images[tf]),
                    filename=f"XAUUSD_{tf}.png"
                ))

        await interaction.followup.send(
            content="⏳ กำลังวิเคราะห์กราฟทั้งหมด รอแป๊บนึงนะครับ...",
            files=files
        )

        analysis = await ai_analyze_chart(images)
        now = datetime.now(TZ_THAI)
        embed = discord.Embed(
            title="🤖 AI วิเคราะห์ XAUUSD — Multi-Timeframe",
            description=analysis,
            color=discord.Color.purple(),
            timestamp=now
        )
        embed.set_footer(text=f"XAU Chart Bot • {now.strftime('%d/%m/%Y %H:%M')} น. • powered by Claude AI")
        await interaction.followup.send(embed=embed)

# ============================================================
#  SLASH COMMAND
# ============================================================
@tree.command(name="chart", description="ดึงกราฟ XAUUSD พร้อมปุ่มวิเคราะห์")
async def cmd_chart(interaction: discord.Interaction):
    now = datetime.now(TZ_THAI)
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
    embed.add_field(name="Symbol", value="`XAUUSD`", inline=True)
    embed.add_field(name="Indicators", value="EMA 20/50/200 + Stochastic", inline=True)
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

bot.run(BOT_TOKEN)
