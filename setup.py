"""UC Learn & Student Life Automation — package setup."""

from setuptools import setup, find_packages

setup(
    name="uc-automation",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "playwright>=1.40.0",
        "icalendar>=5.0.0",
        "openpyxl>=3.1.0",
        "requests>=2.31.0",
        "beautifulsoup4>=4.12.0",
        "lxml>=4.9.0",
        "python-dotenv>=1.0.0",
        "rich>=13.0.0",
        "schedule>=1.2.0",
        "aiofiles>=23.0.0",
    ],
    entry_points={
        "console_scripts": [
            "uc-login=uc_automation.session:cmd_login",
            "uc-digest=scripts.daily_digest:main",
            "uc-grades=scripts.check_grades:main",
            "uc-calendar=scripts.build_calendar:main",
            "uc-download=scripts.download_materials:main",
            "uc-diff=scripts.check_new_content:main",
        ],
    },
    python_requires=">=3.11",
)
