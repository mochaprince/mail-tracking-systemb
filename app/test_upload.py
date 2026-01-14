import requests

def test_upload():
    url = "http://localhost:8000/upload"
    files = {
        "file": ("sample_upload.xlsx", open("backend/app/sample_upload.xlsx", "rb"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    }
    response = requests.post(url, files=files)
    print("Status Code:", response.status_code)
    print("Response JSON:", response.json())

if __name__ == "__main__":
    test_upload()
    