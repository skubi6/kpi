import glob
import os
import shutil

dest_dir = "./jsapp/fonts/"


def create_folder_if_not_exists():
    if not os.path.exists(dest_dir):
        try:
            os.makedirs(dest_dir)
            print("Destination folder has been created!")
        except Exception as e:
            print("Could not create fonts folder - Error: {}".format(str(e)))


def copy_fonts():

    create_folder_if_not_exists()

    print("Copying fonts...")

    for file in glob.glob("./node_modules/font-awesome/fonts/*.*"):
        print(file)
        shutil.copy(file, dest_dir)
    for file in glob.glob("./node_modules/roboto-fontface/fonts/roboto/*.wof*"):
        print(file)
        shutil.copy(file, dest_dir)

    print("DONE")


copy_fonts()
