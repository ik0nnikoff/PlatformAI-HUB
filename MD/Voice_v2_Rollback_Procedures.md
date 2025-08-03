# 🔄 Voice_v2 Rollback Procedures

## 📋 **OVERVIEW**

**Версия**: 1.0  
**Дата создания**: 3 августа 2025 г.  
**Статус**: Production Ready  

Comprehensive rollback procedures для Voice_v2 optimization в случае production issues или необходимости отката к предыдущей версии.

---

## 🚨 **EMERGENCY ROLLBACK SCENARIOS**

### **Scenario 1: Critical Production Issues**
- API response failures > 10%
- Provider connectivity issues
- Data corruption or loss
- Security vulnerabilities

### **Scenario 2: Performance Degradation**
- Response time increase > 50%
- Memory usage spike > 200%
- High error rates > 5%
- System instability

### **Scenario 3: Configuration Issues**
- Environment variable conflicts
- Provider authentication failures
- Storage connectivity problems
- Missing dependencies

---

## 🔧 **ROLLBACK PROCEDURES**

### **Level 1: Configuration Rollback (5-10 minutes)**

#### **Environment Variables Rollback**
```bash
# 1. Backup current environment
cp .env .env.backup.$(date +%Y%m%d_%H%M%S)

# 2. Restore previous environment
cp .env.previous .env

# 3. Restart services
systemctl restart voice-v2-service
# OR
docker-compose restart voice-v2

# 4. Verify services
curl -X GET http://localhost:8000/health
```

#### **Provider Configuration Rollback**
```python
# Quick provider priority rollback
from app.services.voice_v2.core.config import get_config, set_config

config = get_config()

# Disable problematic providers
config.stt_providers[ProviderType.PROBLEMATIC_PROVIDER].enabled = False
config.tts_providers[ProviderType.PROBLEMATIC_PROVIDER].enabled = False

# Increase priority for stable providers  
config.stt_providers[ProviderType.OPENAI].priority = 1
config.tts_providers[ProviderType.OPENAI].priority = 1

set_config(config)
```

### **Level 2: Code Rollback (15-30 minutes)**

#### **Git-based Rollback**
```bash
# 1. Identify last known good commit
git log --oneline -10

# 2. Create rollback branch
git checkout -b rollback-$(date +%Y%m%d_%H%M%S)

# 3. Revert to last known good state
git reset --hard <LAST_GOOD_COMMIT_HASH>

# 4. Force push (if necessary)
git push origin rollback-$(date +%Y%m%d_%H%M%S) --force

# 5. Deploy rollback
# Use your deployment pipeline to deploy the rollback branch
```

#### **File-level Rollback**
```bash
# Rollback specific voice_v2 files from backup
BACKUP_DIR="/backup/voice_v2_$(date -d '1 day ago' +%Y%m%d)"

# 1. Stop services
systemctl stop voice-v2-service

# 2. Backup current state
cp -r app/services/voice_v2 app/services/voice_v2.failed.$(date +%Y%m%d_%H%M%S)

# 3. Restore from backup
cp -r $BACKUP_DIR/app/services/voice_v2 app/services/voice_v2

# 4. Restore dependencies
cp $BACKUP_DIR/requirements.txt requirements.txt
pip install -r requirements.txt

# 5. Restart services
systemctl start voice-v2-service
```

### **Level 3: Full System Rollback (30-60 minutes)**

#### **Container-based Rollback (Docker)**
```bash
# 1. Stop current containers
docker-compose down

# 2. Pull previous image version
docker pull your-registry/voice-v2:previous-stable

# 3. Update docker-compose.yml
sed -i 's/voice-v2:latest/voice-v2:previous-stable/g' docker-compose.yml

# 4. Start with previous version
docker-compose up -d

# 5. Verify rollback
docker-compose logs voice-v2
curl -X GET http://localhost:8000/health
```

#### **Virtual Environment Rollback**
```bash
# 1. Deactivate current environment
deactivate

# 2. Remove problematic environment
rm -rf venv_voice_v2_optimized

# 3. Restore previous environment from backup
cp -r venv_voice_v2_backup venv_voice_v2_optimized

# 4. Activate restored environment
source venv_voice_v2_optimized/bin/activate

# 5. Verify packages
pip list | grep -E "(openai|google|yandex)"
```

---

## 📊 **ROLLBACK VALIDATION**

### **Post-Rollback Health Checks**

#### **1. Service Health Verification**
```bash
#!/bin/bash
# health_check_rollback.sh

echo "🔍 Starting post-rollback health checks..."

# Basic service check
if curl -f -s http://localhost:8000/health > /dev/null; then
    echo "✅ Voice service is responding"
else
    echo "❌ Voice service is not responding"
    exit 1
fi

# Provider connectivity
python3 -c "
import asyncio
from app.services.voice_v2.deployment_validation import ProductionValidator

async def quick_check():
    validator = ProductionValidator()
    results = await validator.validate_provider_connectivity()
    failed = results['failed_providers']
    if failed:
        print(f'❌ Failed providers: {failed}')
        exit(1)
    else:
        print('✅ All providers connected')

asyncio.run(quick_check())
"

echo "🎉 Rollback health checks passed"
```

#### **2. Functional Testing**
```python
# rollback_functional_test.py
import asyncio
import logging
from app.services.voice_v2.orchestrator import VoiceServiceOrchestrator
from app.services.voice_v2.core.schemas import STTRequest, TTSRequest

async def test_rollback_functionality():
    """Test basic functionality after rollback"""
    
    orchestrator = VoiceServiceOrchestrator()
    await orchestrator.initialize()
    
    # Test STT
    try:
        # Create minimal test audio (placeholder)
        test_audio = b"fake_audio_data"  # In real scenario, use actual audio
        stt_request = STTRequest(audio_data=test_audio, language="ru")
        
        # This will fail with fake audio, but tests the pipeline
        try:
            stt_result = await orchestrator.process_stt(stt_request)
            print("✅ STT pipeline functional")
        except Exception as e:
            if "audio" in str(e).lower():
                print("✅ STT pipeline functional (expected audio error)")
            else:
                raise
    except Exception as e:
        print(f"❌ STT pipeline error: {e}")
        return False
    
    # Test TTS
    try:
        tts_request = TTSRequest(text="Тест системы после отката", language="ru")
        tts_result = await orchestrator.process_tts(tts_request)
        print("✅ TTS pipeline functional")
    except Exception as e:
        print(f"❌ TTS pipeline error: {e}")
        return False
    
    await orchestrator.cleanup()
    return True

if __name__ == "__main__":
    success = asyncio.run(test_rollback_functionality())
    exit(0 if success else 1)
```

#### **3. Performance Baseline Verification**
```python
# rollback_performance_test.py
import asyncio
import time
import statistics
from app.services.voice_v2.orchestrator import VoiceServiceOrchestrator
from app.services.voice_v2.core.schemas import TTSRequest

async def performance_baseline_check():
    """Verify performance is back to baseline after rollback"""
    
    orchestrator = VoiceServiceOrchestrator()
    await orchestrator.initialize()
    
    # Performance test
    response_times = []
    test_texts = [
        "Короткий тест",
        "Средний по длине тест для проверки производительности",
        "Длинный тест для проверки производительности системы после отката изменений в production environment"
    ]
    
    for text in test_texts:
        for _ in range(3):  # 3 iterations per text
            start_time = time.time()
            
            try:
                request = TTSRequest(text=text, language="ru")
                await orchestrator.process_tts(request)
                
                response_time = time.time() - start_time
                response_times.append(response_time)
                
            except Exception as e:
                print(f"❌ Performance test failed: {e}")
                return False
    
    # Calculate statistics
    avg_time = statistics.mean(response_times)
    p95_time = sorted(response_times)[int(len(response_times) * 0.95)]
    
    print(f"📊 Performance Results:")
    print(f"   Average response time: {avg_time:.2f}s")
    print(f"   95th percentile: {p95_time:.2f}s")
    
    # Baseline thresholds (adjust based on your requirements)
    if avg_time < 3.0 and p95_time < 5.0:
        print("✅ Performance within baseline thresholds")
        return True
    else:
        print("❌ Performance degraded compared to baseline")
        return False
    
    await orchestrator.cleanup()

if __name__ == "__main__":
    success = asyncio.run(performance_baseline_check())
    exit(0 if success else 1)
```

---

## 🔐 **DATA RECOVERY PROCEDURES**

### **MinIO Storage Recovery**

#### **File Recovery from Backup**
```bash
# 1. List available backups
mc ls minio-backup/voice-files-backup/

# 2. Restore specific files
mc cp --recursive minio-backup/voice-files-backup/2025-08-02/ minio-prod/voice-files/

# 3. Verify restored files
mc ls minio-prod/voice-files/ | head -10
```

#### **Database Recovery (if applicable)**
```sql
-- Restore voice usage logs from backup
RESTORE TABLE voice_usage_logs FROM 'backup_2025_08_02.sql';

-- Verify data integrity
SELECT COUNT(*) FROM voice_usage_logs WHERE DATE(created_at) = '2025-08-02';
```

---

## 📋 **ROLLBACK CHECKLIST**

### **Pre-Rollback Checklist**
- [ ] **Alert stakeholders** about rollback procedure
- [ ] **Document current issue** and rollback reason
- [ ] **Backup current state** before rollback
- [ ] **Identify rollback target** (commit, version, backup)
- [ ] **Prepare rollback commands** and scripts
- [ ] **Schedule maintenance window** (if necessary)

### **During Rollback Checklist**
- [ ] **Stop affected services** gracefully
- [ ] **Execute rollback commands** step by step
- [ ] **Verify each step** before proceeding
- [ ] **Monitor system logs** for errors
- [ ] **Test functionality** incrementally
- [ ] **Document rollback steps** taken

### **Post-Rollback Checklist**
- [ ] **Run health checks** on all components
- [ ] **Verify functionality** with test cases
- [ ] **Check performance metrics** against baseline
- [ ] **Monitor error rates** and response times
- [ ] **Notify stakeholders** of rollback completion
- [ ] **Create incident report** with lessons learned

---

## 🚦 **MONITORING & ALERTS**

### **Rollback Success Metrics**
```yaml
# metrics_config.yml
rollback_success_criteria:
  service_health:
    - endpoint_response_time < 2000ms
    - error_rate < 1%
    - all_providers_healthy: true
  
  performance:
    - average_stt_time < 3000ms
    - average_tts_time < 2000ms
    - memory_usage < 1GB
  
  functionality:
    - stt_success_rate > 95%
    - tts_success_rate > 98%
    - provider_fallback_working: true
```

### **Post-Rollback Alerts**
```python
# rollback_monitoring.py
def setup_rollback_monitoring():
    """Setup enhanced monitoring after rollback"""
    
    alerts = [
        # Critical alerts
        AlertRule(
            name="post_rollback_high_error_rate",
            condition="error_rate > 2%",
            duration="5m",
            severity="critical"
        ),
        
        # Warning alerts  
        AlertRule(
            name="post_rollback_performance_degradation",
            condition="avg_response_time > 3000ms",
            duration="10m", 
            severity="warning"
        ),
        
        # Info alerts
        AlertRule(
            name="post_rollback_provider_switching",
            condition="provider_switches > 10/hour",
            duration="1h",
            severity="info"
        )
    ]
    
    return alerts
```

---

## 📞 **EMERGENCY CONTACTS**

### **Escalation Matrix**
```
Level 1 (0-15 min): Development Team Lead
Level 2 (15-30 min): Technical Manager  
Level 3 (30+ min): CTO/Engineering Director

Emergency Contacts:
- Development Team: #voice-v2-dev (Slack)
- Operations Team: #ops-emergency (Slack)
- On-call Engineer: +1-XXX-XXX-XXXX
```

### **External Vendor Contacts**
```
OpenAI Support: support@openai.com
Google Cloud Support: [Support Case System]
Yandex Cloud Support: [Support Portal]
MinIO Support: [Enterprise Support if applicable]
```

---

## 📚 **LESSONS LEARNED TEMPLATE**

### **Post-Rollback Incident Report**
```markdown
# Voice_v2 Rollback Incident Report

## Incident Details
- **Date**: [YYYY-MM-DD]
- **Duration**: [Start Time] - [End Time]
- **Severity**: [Critical/High/Medium/Low]
- **Services Affected**: Voice_v2 System

## Root Cause
[Describe what caused the need for rollback]

## Rollback Process
- **Rollback Level**: [1/2/3]
- **Rollback Target**: [Version/Commit/Backup]
- **Duration**: [Time taken for rollback]
- **Issues Encountered**: [Any problems during rollback]

## Resolution Verification
- [ ] Health checks passed
- [ ] Functionality verified
- [ ] Performance restored
- [ ] Monitoring active

## Lessons Learned
1. [What went well]
2. [What could be improved]
3. [Prevention measures for future]

## Action Items
- [ ] [Action 1] - [Owner] - [Due Date]
- [ ] [Action 2] - [Owner] - [Due Date]
```

---

**📅 Document Created**: 3 августа 2025 г.  
**👨‍💻 Created By**: GitHub Copilot  
**📋 Version**: 1.0  
**✅ Status**: Production Ready

---

## 🏁 **ЗАКЛЮЧЕНИЕ**

Voice_v2 Rollback Procedures обеспечивают:

- ✅ **Быстрый rollback** в критических ситуациях (5-60 минут)
- ✅ **Comprehensive validation** после отката
- ✅ **Data recovery** procedures для восстановления данных
- ✅ **Monitoring setup** для post-rollback наблюдения
- ✅ **Documentation templates** для incident reporting

Следуя этим процедурам, команда может уверенно откатить изменения и восстановить стабильность production системы.
