import datetime
import asyncio
from typing import Dict, Any
from .base import NativeTool

class DateTimeTool(NativeTool):
    """Tool for getting current local time, date, and day of week."""

    @property
    def name(self) -> str:
        return "date_time"

    @property
    def description(self) -> str:
        return "Get the current local time, date, day of the week, or year."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["time", "date", "full"],
                    "description": "What to return: 'time' (hours and minutes), 'date' (day, month, year), or 'full'.",
                },
            },
            "required": [],
        }

    async def execute(self, args: Dict[str, Any]) -> str:
        fmt = args.get("format", "full")
        now = datetime.datetime.now()

        days_it = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
        months_it = [
            "", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
            "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"
        ]

        day_name = days_it[now.weekday()]
        month_name = months_it[now.month]

        time_str = now.strftime("%H:%M")
        date_str = f"{day_name} {now.day} {month_name} {now.year}"

        def _exec():
            if fmt == "time":
                return f"Sono le ore {time_str}."
            elif fmt == "date":
                return f"Oggi è {date_str}."
            else:
                return f"Oggi è {date_str} e sono le ore {time_str}."

        return await asyncio.to_thread(_exec)
