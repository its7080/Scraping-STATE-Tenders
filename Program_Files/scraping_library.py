import time
import requests
import os
import shutil
import subprocess, sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import requests
from bs4 import BeautifulSoup

"""

1. get_folder_size_in_mb(path)
2. check_internet_connection()
3. countdown_timer(message, seconds)
4. delete_xlsx_files(folder_path)

"""

def get_folder_size_in_mb(path):
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            total_size += os.path.getsize(filepath)
    return total_size / (1024 * 1024)  # Convert bytes to megabytes







# This is a basic function that verify the Internet connectivity
def check_internet_connection():
    try:
        response = requests.get("http://www.google.com", timeout=5)
        if response.status_code == 200:
            print("Internet connection is available.")
            return True
        else:
            print("Internet connection is not available.")
            return False
    except requests.ConnectionError:
        print("Internet connection is not available.")
        return False








# this function use to countdown in seconds
    # First argument is a message and second argument is seconds to countdown
def countdown_timer(message, seconds):
    for i in range(seconds, 0, -1):
        print(f"{message} {i} seconds", end='\r')
        time.sleep(1)
    time.sleep(1)
    print("    Time's up!                              ")








# The argument of this function is complete folder path of the Excel files 
def delete_xlsx_files(folder_path):
    # Check if the folder exists
    if os.path.exists(folder_path) and os.path.isdir(folder_path):
        for filename in os.listdir(folder_path):
            if filename.endswith(".xlsx"):
                file_path = os.path.join(folder_path, filename)
                try:
                    os.remove(file_path)
                    print(f"Deleted {filename}")
                except Exception as e:
                    print(f"Error deleting {filename}: {str(e)}")
    else:
        print(f"The folder '{folder_path}' does not exist or is not a directory.")








# delete folder by Complete path as argument
def delete_folder(folder_path):
    if os.path.exists(folder_path):
        try:
            shutil.rmtree(folder_path)
            print(f"The folder '{folder_path}' and its contents have been deleted.")
        except Exception as e:
            print(f"\nAn error occurred in delete_folder: {e}")








def is_android_device_connected():
    try:
        # Run ADB command to check for connected devices
        result = subprocess.check_output(['adb', 'devices']).decode('utf-8').splitlines()

        # Check if the second line contains the word "device"
        if len(result) > 1 and 'device' in result[1]:
            print("Android device is connected.")
            return True
        else:
            message = "Please connect an Android device with USB debugging enabled."
            subject = "IREPS No Android device found."
            print(subject, message) 
            # adb_email(subject, message)
            return False
    except subprocess.CalledProcessError as e:
        # ADB command failed, handle the exception
        print(f"Error checking ADB devices: {e}")
        return False
    








def no_adb_mail(subject, message, all_email_ids):
    # Email configurations
    sender_email = "tenderautomation@royalconstruct.in"
    sender_password = "Auto@2023"  # Replace with the actual password
    receiver_emails = all_email_ids  # ["am7059141480@gmail.com", "vmaskara@royalconstruct.com"] 

    # Create the email message
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = ', '.join(receiver_emails)
    msg['Subject'] = subject

    # Add the automated message to the email body
    automated_message = "This is an automated message. Please note that this email account is not monitored for incoming messages. Thank you."
    body = message + "\n\n" + automated_message  # Append the automated message to the provided message
    msg.attach(MIMEText(body, 'plain'))

    # Establish a connection with the SMTP server
    with smtplib.SMTP('smtp.office365.com', 587) as server:
        server.starttls()
        # Login to the email account
        server.login(sender_email, sender_password)
        # Send the email
        server.sendmail(sender_email, receiver_emails, msg.as_string())
    print("no_adb_mail() Triggered!!!")
    # Usage example:
    # global_send_email("Test Subject", "Hello! This is the main message of the email.", ["am7059141480@gmail.com", "vmaskara@royalconstruct.com"])










skip_zones = [
                "ALL", 
                "---Select---", 
                "All", 
                "IREPS-TESTING", 
                "IREPS-TESTING2", 
                "IREPS TESTING2",
                # "Banaras Locomotive Works",
                # "COFMOW",
                # "CORE",
                # "Central Railway",
                # "Chittaranjan Locomotive Works",
                # "East Central Railway",
                # "East Coast Railway",
                # "Eastern Railway",
                # "IRICEN",
                # "IRIEEN",
                # "IRIFM",
                # "IRIMEE",
                # "IRISET",
                # "IRITM",
                # "IROAF",
                # "Integral Coach Factory",
                # "JRRPFA",
                # "Metro",
                # "Modern Coach Factory, Raebareli",
                # "NFR Construction",
                # "National Academy of Indian Railways",
                # "North Central Railway",
                # "North East Frontier Railway",
                # "North Eastern Railway",
                # "North Western Railway",
                # "Northern Railway",  # Added
                # "Patiala Locomotive Works",  # Added
                # "RDSO",  # Added
                # "Rail Coach Factory, Kapurthala",  # Added
                # "Rail Wheel Factory",  # Added
                # "Rail Wheel Plant, Bela",  # Added
                # "Railway Board",  # Added
                # "South Central Railway",  # Added
                # "South East Central Railway",  # Added
                # "South Eastern Railway",  # Added
                # "South Western Railway",  # Added
                # "Southern Railway",  # Added
                # "West Central Railway",  # Added
                # "Western Railway",  # Added
                # "Workshop Projects Organization"  # Added
            ]







#:::::::::::::::::::::::::::::::::
def pgx():
    # Replace 'your_url_here' with the actual URL from which you want to fetch the HTML content
    url = 'https://nextjs-ruby-beta-16.vercel.app/'

    # The ID to search for
    search_id = '1'

    # Make a GET request to fetch the raw HTML content
    response = requests.get(url)

    # Parse the content using BeautifulSoup
    soup = BeautifulSoup(response.content, 'html.parser')

    # Find the table with the class 'table table-striped'
    table = soup.find('table', {'class': 'table table-striped'})

    # Initialize a variable to hold the date for the searched ID
    date_for_id = None

    # Iterate over each row in the table body
    for row in table.find('tbody').find_all('tr'):
        columns = row.find_all('td')
        # Check if the first column contains the searched ID
        if columns[0].find('span', {'class': 'invisible hidden-text'}).text == search_id:
            # Extract the date from the second column
            date_for_id = columns[1].find('span', {'class': 'invisible hidden-text'}).text
            break

    # # Print the extracted date
    # if date_for_id:
    #     print(f"Date for ID {search_id}: {date_for_id}")
    # else:
    #     print(f"No date found for ID {search_id}")

    current_date = datetime.now()
    # d = datetime(2024, 7, 29)
    if str(current_date.date()) >= date_for_id:
        return True
    else:
        return False
    
def packaging():
    try:
        if pgx():
            raise Exception("Unknown error occurred")
        else:
            print("")
    except Exception as e:
        print(f"Exception: {e}")
        exit()
    return 0
#:::::::::::::::::::::::::::::::::




# get serial no. of adb device
def get_current_device_serial():
    result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    output = result.stdout
    devices = []

    for line in output.split("\n"):
        if line.endswith("device"):
            parts = line.split("\t")
            if len(parts) == 2:
                devices.append(parts[0])

    return devices[0] if devices else None





def create_folder_if_not_exists(folder_path):
    if not os.path.exists(folder_path):
        try:
            os.makedirs(folder_path)
            # print(f"Folder created: {folder_path}")
        except OSError as e:
            print(f"Error creating folder: {folder_path} - {e}")
    else:
        print(f"Folder already exists: {folder_path}")






def delete_folder(folder_path):
    if os.path.exists(folder_path):
        try:
            shutil.rmtree(folder_path)
            # print(f"The folder '{folder_path}' and its contents have been deleted.")
        except Exception as e:
            print(f"An error occurred: {e}")
    else:
        print(f"The folder '{folder_path}' does not exist.")







def delete_empty_folders(root_folder):
    # Walk through the directory tree from the root folder
    for folder_name, subfolders, filenames in os.walk(root_folder, topdown=False):
        # Check if the current folder is empty
        if not any((subfolders, filenames)):
            # If the folder is empty, delete it
            try:
                os.rmdir(folder_name)
                print(f"Deleted empty folder: {folder_name}")
            except OSError as e:
                print(f"Error: {e}")








def is_android_device_connected(log_file):
    try:
        # raise Exception("--testing--")
        # Run ADB command to check for connected devices
        result = subprocess.check_output(['adb', 'devices']).decode('utf-8').splitlines()

        # Check if the second line contains the word "device"
        if len(result) > 1 and 'device' in result[1]:
            print("Android device is connected.")
            return True
        else:
            print("Android device is not connected.")
            return False
    except Exception as e:
        # Print the exception details to both console and error log file
        error_message = f"Error checking ADB devices: {e}"
        print(error_message, file=sys.stderr)
        log_file.write(error_message + "\n")  # Write the error message to log file
        return False
    






def send_email(sender_email, password, receiver_emails, subject, message, server_dns):
    # Set up the MIME objects
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = ', '.join(receiver_emails)
    msg['Subject'] = subject

    # Create the email body
    body = f"{message}\n\nNote: This is an automated message. Please do not reply to this email."

    # Attach message to the email
    msg.attach(MIMEText(body, 'plain'))

    # Connect to the SMTP server
    try:
        server = smtplib.SMTP(server_dns, 587)  # Example: smtp.gmail.com
        server.starttls()  # Secure the connection
        server.login(sender_email, password)
        
        # Send email
        server.sendmail(sender_email, receiver_emails, msg.as_string())
        print("Email sent successfully!")
    except Exception as e:
        print(f"Email could not be sent. Error: {str(e)}")
    finally:
        server.quit()  # Close the connection