import requests
import json

response = requests.get('http://localhost:5000/api/jobs')
data = response.json()

print("Status Code:", response.status_code)
print("=" * 60)

if response.status_code == 200:
    print(f"✅ จำนวนงาน: {len(data.get('jobs', []))}")
    print("\n📋 ตัวอย่างข้อมูลงานแรก:")
    
    if data.get('jobs'):
        job = data['jobs'][0]
        print(json.dumps(job, indent=2))
        
        print("\n🔑 Property ที่มี:")
        for key in job.keys():
            print(f"  - {key}")
else:
    print("❌ Error:", data)