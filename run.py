#!/usr/bin/env python3
"""
Quick run script for Autonomous Bug Fixer
Just run: python run.py
"""

import sys
import os
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Import and run main
from main import main, setup_logging

if __name__ == "__main__":
    print("""
    ╔════════════════════════════════════════════╗
    ║   🤖 Autonomous Bug Fixer - CrewAI        ║
    ║   Intelligent Multi-Agent Bug Fixing      ║
    ╚════════════════════════════════════════════╝
    """)
    
    # Check for .env file
    env_file = Path('.env')
    if not env_file.exists():
        print("⚠️  WARNING: .env file not found!")
        print("   Please create .env from .env.example")
        print("   Example:")
        print("     cp .env.example .env")
        print("     # Then edit .env with your settings\n")
        
        response = input("Continue anyway? (y/n): ")
        if response.lower() != 'y':
            print("Exiting...")
            sys.exit(0)
    
    # Setup logging
    setup_logging()
    
    # Run main
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
