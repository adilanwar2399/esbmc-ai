#!/usr/bin/env python3

# Author: Yiannis Charalambous 2023

import os

import argparse
import openai
from subprocess import Popen, PIPE

from src.loading_widget import LoadingWidget
import src.config as config
from src.config import printv
from src.chat import ChatInterface, SYSTEM_MSG_DEFAULT

HELP_MESSAGE: str = """Tool that passes ESBMC output into ChatGPT and allows for natural language
explanations. Type /help in order to view available commands."""

DEFAULT_PROMPT: str = "Walk me through the source code, while also explaining the output of ESBMC at the relevant parts. You shall not start the reply with an acknowledgement message such as 'Certainly'."


def print_help() -> None:
    print()
    print("Commands:")
    print("/help: Print this help message.")
    print("/exit: Exit the program.")
    print()


def init_check_health(verbose: bool) -> None:
    def printv(m) -> None:
        if verbose:
            print(m)

    printv("Performing init health check...")
    # Check that the .env file exists.
    if os.path.exists(".env"):
        printv("Environment file has been located")
    else:
        print("Error: .env file is not found in project directory")
        exit(3)


def check_health() -> None:
    printv("Performing health check...")
    # Check that ESBMC exists.
    if os.path.exists(config.esbmc_path):
        printv("ESBMC has been located")
    else:
        print(f"Error: ESBMC could not be found in {config.esbmc_path}")
        exit(3)


def get_src(path: str) -> str:
    with open(path, mode="r") as file:
        content = file.read()
        return str(content)


def esbmc(path: str, esbmc_params: list = config.esbmc_params):
    # Build parameters
    esbmc_cmd = [config.esbmc_path]
    esbmc_cmd.extend(esbmc_params)
    esbmc_cmd.append(path)

    # Run ESBMC and get output
    process = Popen(esbmc_cmd, stdout=PIPE)
    (output_bytes, err_bytes) = process.communicate()
    # Return
    exit_code = process.wait()
    output: str = str(output_bytes).replace("\\n", "\n")
    err: str = str(err_bytes).replace("\\n", "\n")
    return exit_code, output, err


def print_assistant_response(chat: ChatInterface, response) -> None:
    if config.raw_responses:
        print(response)
        return

    if config.verbose:
        total_tokens: int = response.usage.total_tokens
        max_tokens: int = chat.max_tokens
        finish_reason: str = response.choices[0].finish_reason
        print(
            "stats:",
            f"total tokens: {total_tokens},",
            f"max tokens: {max_tokens}",
            f"finish reason: {finish_reason}\n",
        )

    response_role = response.choices[0].message.role
    response_message = response.choices[0].message.content
    print(f"{response_role}: {response_message}\n")


def build_system_messages(source_code: str, esbmc_output: str) -> list:
    """Build the setup messages from either the provided default settings or from
    the loaded files."""
    printv("Loading system messages")
    system_messages: list = []
    if config.cfg_sys_msg != "":
        system_messages.extend(config.cfg_sys_msg)
    else:
        system_messages.extend(SYSTEM_MSG_DEFAULT)

    system_messages.extend(
        [
            {
                "role": "system",
                "content": f"Reply OK if you understand that the following text is the program source code: {source_code}",
            },
            {"role": "assistant", "content": "OK"},
            {
                "role": "system",
                "content": f"Reply OK if you understand that the following text is the output from ESBMC after reading the program source code: {esbmc_output}",
            },
            {"role": "assistant", "content": "OK"},
        ]
    )

    return system_messages


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ESBMC-ChatGPT",
        description=HELP_MESSAGE,
        epilog="Made by Yiannis Charalambous",
    )

    parser.add_argument("filename", help="The filename to pass to esbmc.")
    parser.add_argument(
        "remaining",
        nargs=argparse.REMAINDER,
        help="Any arguments after the filename will be passed to ESBMC as parameters.",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Output will be verbose.",
    )

    parser.add_argument(
        "-r",
        "--raw-output",
        action="store_true",
        default=False,
        help="Responses from AI model will be shown raw.",
    )

    parser.add_argument(
        "-m",
        "--ai-model",
        default="",
        help="Which AI model to use.",
    )

    args = parser.parse_args()

    print("ESBMC-AI")
    print()

    init_check_health(args.verbose)

    config.load_envs()
    config.load_args(args)

    check_health()

    config.init_ai_data()

    anim: LoadingWidget = LoadingWidget()

    # Read the source code and esbmc output.
    printv("Reading source code...")
    printv(f"Running ESBMC with {config.esbmc_params}")
    source_code: str = get_src(args.filename)

    anim.start("ESBMC is processing... Please Wait")
    exit_code, esbmc_output, esbmc_err = esbmc(args.filename)
    anim.stop()

    # ESBMC will output 0 for verification success and 1 for verification
    # failed, if anything else gets thrown, it's an ESBMC error.
    if exit_code == 0:
        print("Success!")
        print(esbmc_output)
        exit(0)
    elif exit_code != 1:
        print(f"ESBMC exit code: {exit_code}")
        exit(1)

    # Prepare the chat.
    printv("Initializing OpenAI")
    openai.api_key = config.openai_api_key

    # Inject output for ai.
    system_messages: list = build_system_messages(source_code, esbmc_output)

    chat = ChatInterface(
        system_messages=system_messages,
        model=config.ai_model,
        temperature=config.chat_temperature,
    )
    printv(f"Using AI Model: {chat.model_name}")

    # Show the initial output.
    response = {}
    if config.cfg_initial_prompt:
        printv("Using initial prompt from file...\n")
        anim.start("Model is parsing ESBMC output... Please Wait")
        response = chat.send_message(config.cfg_initial_prompt)
        anim.stop()
    else:
        printv("Using default initial prompts...\n")
        anim.start("Model is parsing ESBMC output... Please Wait")
        response = chat.send_message(DEFAULT_PROMPT)
        anim.stop()

    print_assistant_response(chat, response)

    while True:
        user_message = input(">: ")
        if user_message == "/exit":
            print("exiting...")
            exit(0)
        elif user_message == "/help":
            print_help()
            continue
        elif user_message == "":
            continue
        else:
            print()

        anim.start("Generating response... Please Wait")
        response = chat.send_message(user_message)
        anim.stop()

        print_assistant_response(chat, response)


if __name__ == "__main__":
    main()
