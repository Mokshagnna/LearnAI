import requests
import time
import subprocess
import sys

if __name__ == "__main__":
    # Start server
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8001'], 
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(4)

    try:
        r = requests.get('http://127.0.0.1:8001/api/courses')
        print('Courses:', r.status_code)
        data = r.json()
        print('Number of courses:', len(data.get('courses', [])))
        
        # Test quiz generation
        course = data['courses'][0]
        topic = course['topics'][0]
        print('Testing quiz for {} - {}'.format(course['name'], topic['name']))
        
        r = requests.post('http://127.0.0.1:8001/api/quiz/generate', 
                          json={'course_code': course['code'], 'topic': topic['name'], 'student_id': 'test-student-1'})
        print('Quiz generate:', r.status_code)
        quiz = r.json()
        print('Quiz ID:', quiz.get('quiz_id'))
        print('Questions:', len(quiz.get('questions', [])))
        print('Adaptive applied:', quiz.get('adaptive_applied'))
        print('Recommended difficulty:', quiz.get('recommended_difficulty'))
        
        # Test quiz submit
        answers = [q['options']['A'] for q in quiz['questions']]
        r = requests.post('http://127.0.0.1:8001/api/quiz/submit',
                          json={'quiz_id': quiz['quiz_id'], 'answers': answers, 'student_id': 'test-student-1'})
        print('Quiz submit:', r.status_code)
        result = r.json()
        print('Score:', result.get('score'), '/', result.get('total'))
        print('Percentage:', result.get('percentage'))
        print('Status:', result.get('status'))
        print('Weak concepts:', result.get('weak_concepts'))
        print('Difficulty accuracy:', result.get('difficulty_accuracy'))
        
        # Test history
        r = requests.get('http://127.0.0.1:8001/api/student/test-student-1/history')
        print('History:', r.status_code, len(r.json().get('attempts', [])))
        
        # Test performance
        r = requests.get('http://127.0.0.1:8001/api/student/test-student-1/performance')
        print('Performance:', r.status_code)
        perf = r.json()
        print('Concept perf:', len(perf.get('concept_performance', {})))
        print('Diff perf:', perf.get('difficulty_performance', {}))
        
    finally:
        proc.terminate()